"""
Motor de backtest para "FVG Continuación" — a diferencia de IFVG Sniper
(estrategias/ifvg_sniper), acá NO se espera a que el gap se rompa e
invierta. Se opera el retest del FVG ORIGINAL como soporte/resistencia,
a favor del impulso que lo creó (estrategia de continuación, no de
reversión):

  - Gap alcista (3 velas) → zona de DEMANDA por debajo del precio. Al
    retestearla, se busca LONG (a favor del impulso alcista original).
  - Gap bajista → zona de OFERTA por encima del precio. Al retestearla,
    se busca SHORT.
  - SL apenas afuera de la zona (del otro lado de donde entra el precio),
    no un múltiplo de ATR desde la entrada como en IFVG Sniper.
  - TP en el próximo swing/liquidez SIN mitigar en la dirección del
    trade (el próximo pivote alto no tocado todavía, para longs; el
    próximo pivote bajo no tocado, para shorts) — no un R:R fijo.
  - Si el precio rompe la zona en contra ANTES de retestearla, la zona
    se invalida (a diferencia de IFVG, que ahí la invierte y arma un
    setup de reversión — acá simplemente se descarta, esto es sólo
    continuación).

Reglas replicadas/reusadas de ifvg_sniper: ATR de Wilder, filtros de
forma del gap (cuerpo/rango de la vela de impulso), entrada por ORDEN
LÍMITE (la señal se decide al cierre de la vela, se llena desde la vela
siguiente en adelante — sin look-ahead), una operación a la vez, corte
de sesión, comisión+slippage reales.

Detección de pivotes (swings): réplica de ta.pivothigh/ta.pivotlow de
Pine (ver upf_artillery/backtest/engine.py) — un pivote en la barra j
recién se CONFIRMA `piv_right` barras después, en la barra j+piv_right;
no hay look-ahead porque el pivote no existe en el histórico del motor
hasta esa barra de confirmación.

Simplificaciones (documentadas, no escondidas):
  - "Próximo swing sin mitigar" se aproxima con una lista de pivotes
    confirmados recientes (ver `max_swing_lookback`), no con un análisis
    completo de estructura de mercado (HH/HL/LH/LL, order blocks, etc.).
  - Si no hay ningún swing sin mitigar disponible como objetivo cuando
    aparece un retest válido, esa zona simplemente no opera esa vez (no
    hay TP de respaldo por ATR ni nada por el estilo) — se descarta el
    trade, no la zona (se puede reintentar en un retest posterior si
    para entonces ya apareció un swing target).
  - Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
    primero (supuesto conservador, igual que en ifvg_sniper/upf_artillery).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Params:
    max_fvg_age: int = 60
    min_gap_atr: float = 0.75
    min_body_ratio: float = 0.5
    min_range_atr: float = 0.6
    invalidate_buffer_atr: float = 0.05  # cuánto tiene que romper el precio la zona en contra para invalidarla
    touch_tol_atr: float = 0.05          # entra un poco antes del borde exacto de la zona
    sl_buffer_atr: float = 0.05          # SL = borde de la zona +/- este buffer (en ATR)
    atr_len: int = 14

    piv_left: int = 3
    piv_right: int = 2
    max_swing_lookback: int = 300  # cuántos pivotes recientes se guardan en memoria como objetivos candidatos
    min_rr: float = 1.0            # descarta el trade si el swing target más cercano da menos de este R:R

    max_risk_usd: float = 300.0
    point_value_usd: float = 2.0
    max_qty: int = 40

    use_session_close: bool = True
    session_tz: str = "America/New_York"
    session_close_hour: int = 16
    session_close_minute: int = 45

    entry_start_hour: Optional[float] = None
    entry_end_hour: Optional[float] = None

    commission_round_turn_usd: float = 0.0
    slippage_ticks: float = 0.0
    tick_size: float = 0.25

    tie_break: str = "sl_first"  # "sl_first" (conservador) | "tp_first" (cota optimista)

    # Dónde referenciar el SL:
    #   "zone"            = borde de la zona del FVG (comportamiento original)
    #   "impulse_candle"  = extremo de la vela de impulso completa (i-1), que
    #                       suele quedar más lejos que el borde de la zona —
    #                       pensado para no saltar por una mecha de barrido de
    #                       liquidez que sólo toca ligeramente la zona antes de
    #                       continuar a favor del impulso original.
    sl_mode: str = "zone"


@dataclass
class Zone:
    id: int
    top: float
    bot: float
    is_demand: bool  # True = zona de demanda (busca long) | False = zona de oferta (busca short)
    created_bar: int
    impulse_low: float = np.nan   # low/high de la vela de impulso (i-1) que creó el gap
    impulse_high: float = np.nan
    active: bool = True
    used: bool = False


@dataclass
class Swing:
    bar: int
    price: float
    is_high: bool
    mitigated: bool = False


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    vals = np.full(len(tr), np.nan, dtype=float)
    tr_vals = tr.to_numpy(dtype=float)
    if len(tr) >= length:
        prev = tr_vals[:length].mean()
        vals[length - 1] = prev
        for i in range(length, len(tr)):
            prev = (prev * (length - 1) + tr_vals[i]) / length
            vals[i] = prev
    return pd.Series(vals, index=tr.index)


def pivots(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Réplica de ta.pivothigh/ta.pivotlow: devuelve, por barra, el valor del
    pivote si ESA barra es exactamente donde se confirma uno (right barras
    después del extremo real), o NaN si no. No repinta: en la práctica, el
    pivote de la barra j no se conoce hasta la barra j+right.
    """
    n = len(high)
    window = left + right + 1
    piv_h = np.full(n, np.nan)
    piv_l = np.full(n, np.nan)
    if n < window:
        return piv_h, piv_l
    h_s, l_s = pd.Series(high), pd.Series(low)
    roll_max = h_s.rolling(window).max().to_numpy()
    roll_min = l_s.rolling(window).min().to_numpy()
    for i in range(window - 1, n):
        j = i - right
        if high[j] == roll_max[i]:
            piv_h[i] = high[j]
        if low[j] == roll_min[i]:
            piv_l[i] = low[j]
    return piv_h, piv_l


def _within_session(ts: pd.Timestamp, p: Params) -> bool:
    if not p.use_session_close:
        return True
    local = ts.tz_convert(p.session_tz)
    minutes_now = local.hour * 60 + local.minute
    cutoff = p.session_close_hour * 60 + p.session_close_minute
    return minutes_now < cutoff


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """
    df: DataFrame indexado por datetime (tz-aware), ordenado ascendente,
        con columnas open/high/low/close.
    Devuelve (trades_df, resumen).
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    ts = df.index
    n = len(df)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len).values
    piv_h, piv_l = pivots(h, l, p.piv_left, p.piv_right)

    zones: list[Zone] = []
    next_id = 1
    swings: list[Swing] = []  # pivotes confirmados, más nuevos al final

    state = "none"  # none -> waiting -> open
    pending_zone: Optional[Zone] = None
    pending_is_long = False
    pending_limit = np.nan
    pending_sl = np.nan
    pending_tp = np.nan
    pending_qty = 0

    trades = []
    orders_placed = 0
    orders_filled = 0
    entry_price = entry_bar = None

    for i in range(n):
        a = atr[i]
        within = _within_session(ts[i], p)

        # ── 1. Confirmar nuevos pivotes (swings) ────────────────────────────
        if not np.isnan(piv_h[i]):
            swings.append(Swing(i, piv_h[i], is_high=True))
        if not np.isnan(piv_l[i]):
            swings.append(Swing(i, piv_l[i], is_high=False))
        if len(swings) > p.max_swing_lookback:
            swings = swings[-p.max_swing_lookback:]

        # mitigar swings que el precio ya tocó (no sirven más como objetivo)
        for sw in swings:
            if sw.mitigated:
                continue
            if sw.is_high and h[i] >= sw.price:
                sw.mitigated = True
            elif not sw.is_high and l[i] <= sw.price:
                sw.mitigated = True

        # ── 2. Detectar nuevos FVG (necesita al menos 3 velas) ──────────────
        if i >= 2 and not np.isnan(a):
            mid_range = h[i - 1] - l[i - 1]
            body_ratio = abs(c[i - 1] - o[i - 1]) / mid_range if mid_range > 0 else 0.0
            range_atr = mid_range / a if a > 0 else 0.0
            passes_shape = body_ratio >= p.min_body_ratio and range_atr >= p.min_range_atr

            if passes_shape:
                gap_up = l[i] - h[i - 2]
                if gap_up > 0 and a > 0 and gap_up / a >= p.min_gap_atr:
                    zones.append(Zone(next_id, top=l[i], bot=h[i - 2], is_demand=True, created_bar=i,
                                       impulse_low=l[i - 1], impulse_high=h[i - 1]))
                    next_id += 1
                gap_dn = l[i - 2] - h[i]
                if gap_dn > 0 and a > 0 and gap_dn / a >= p.min_gap_atr:
                    zones.append(Zone(next_id, top=l[i - 2], bot=h[i], is_demand=False, created_bar=i,
                                       impulse_low=l[i - 1], impulse_high=h[i - 1]))
                    next_id += 1

        # ── 3. Invalidar/expirar zonas activas no usadas ────────────────────
        buf = a * p.invalidate_buffer_atr if not np.isnan(a) else 0.0
        for z in zones:
            if not z.active or z.used:
                continue
            expired = (i - z.created_bar) > p.max_fvg_age
            invalidated = c[i] < z.bot - buf if z.is_demand else c[i] > z.top + buf
            if expired or invalidated:
                z.active = False

        # ── 4. Orden pendiente: ¿se llenó, se cancela? ──────────────────────
        if state == "waiting":
            if pending_is_long:
                if l[i] <= pending_limit:
                    entry_price = min(pending_limit, o[i]) if o[i] <= pending_limit else pending_limit
                    entry_bar = i
                    state = "open"
                    orders_filled += 1
            else:
                if h[i] >= pending_limit:
                    entry_price = max(pending_limit, o[i]) if o[i] >= pending_limit else pending_limit
                    entry_bar = i
                    state = "open"
                    orders_filled += 1

            if state == "waiting" and (not pending_zone.active or not within):
                state = "none"
                pending_zone = None

        # ── 5. Posición abierta: ¿tocó SL/TP o toca cierre de sesión? ───────
        if state == "open":
            exit_price = None
            reason = None
            if pending_is_long:
                hit_sl, hit_tp = l[i] <= pending_sl, h[i] >= pending_tp
            else:
                hit_sl, hit_tp = h[i] >= pending_sl, l[i] <= pending_tp

            slip = p.slippage_ticks * p.tick_size
            if hit_sl and hit_tp and p.tie_break == "tp_first":
                hit_sl = False
            if hit_sl:
                exit_price, reason = pending_sl, "SL"
            elif hit_tp:
                exit_price, reason = pending_tp, "TP"
            elif not within:
                exit_price, reason = c[i], "sesion"

            if exit_price is not None:
                direction = 1 if pending_is_long else -1
                if reason in ("SL", "sesion"):
                    exit_price = exit_price - direction * slip

                gross_pnl_usd = direction * (exit_price - entry_price) * pending_qty * p.point_value_usd
                commission_usd = p.commission_round_turn_usd * pending_qty
                pnl_usd = gross_pnl_usd - commission_usd
                risk_dist = abs(entry_price - pending_sl)
                risk_usd = risk_dist * pending_qty * p.point_value_usd

                trades.append(
                    {
                        "entry_time": ts[entry_bar],
                        "exit_time": ts[i],
                        "direction": "long" if pending_is_long else "short",
                        "entry": entry_price,
                        "exit": exit_price,
                        "tp_target": pending_tp,
                        "qty": pending_qty,
                        "reason": reason,
                        "gross_pnl_usd": gross_pnl_usd,
                        "commission_usd": commission_usd,
                        "pnl_usd": pnl_usd,
                        "r_multiple": pnl_usd / risk_usd if risk_usd > 0 else np.nan,
                        "bars_held": i - entry_bar,
                    }
                )
                state = "none"
                pending_zone = None

        # ── 6. Buscar una nueva zona elegible si estamos libres ─────────────
        entry_hour_ok = True
        if p.entry_start_hour is not None:
            local = ts[i].tz_convert(p.session_tz)
            hour_now = local.hour + local.minute / 60
            if p.entry_start_hour <= p.entry_end_hour:
                entry_hour_ok = p.entry_start_hour <= hour_now < p.entry_end_hour
            else:
                entry_hour_ok = hour_now >= p.entry_start_hour or hour_now < p.entry_end_hour

        if state == "none" and within and entry_hour_ok and not np.isnan(a):
            tol = a * p.touch_tol_atr
            sl_buf = a * p.sl_buffer_atr
            for z in zones:
                if not (z.active and not z.used):
                    continue
                is_long = z.is_demand
                entry_level = z.top - tol if is_long else z.bot + tol
                if p.sl_mode == "impulse_candle":
                    sl_ref = z.impulse_low if is_long else z.impulse_high
                else:
                    sl_ref = z.bot if is_long else z.top
                sl_level = sl_ref - sl_buf if is_long else sl_ref + sl_buf
                risk_dist = abs(entry_level - sl_level)
                if risk_dist <= 0:
                    continue

                # próximo swing sin mitigar en la dirección del trade, más cercano al entry
                candidates = [
                    sw for sw in swings
                    if not sw.mitigated and sw.bar < i and (
                        (is_long and sw.is_high and sw.price > entry_level)
                        or (not is_long and not sw.is_high and sw.price < entry_level)
                    )
                ]
                if not candidates:
                    continue
                target = min(candidates, key=lambda sw: sw.price) if is_long else max(candidates, key=lambda sw: sw.price)
                reward_dist = abs(target.price - entry_level)
                rr = reward_dist / risk_dist
                if rr < p.min_rr:
                    continue

                risk_usd_per_contract = risk_dist * p.point_value_usd
                qty = (
                    int(min(p.max_qty, np.floor(p.max_risk_usd / risk_usd_per_contract)))
                    if risk_usd_per_contract > 0
                    else 0
                )
                if qty >= 1:
                    z.used = True
                    pending_zone = z
                    pending_is_long = is_long
                    pending_limit = entry_level
                    pending_qty = qty
                    pending_sl = sl_level
                    pending_tp = target.price
                    state = "waiting"
                    orders_placed += 1
                    break

        if len(zones) > 5000:
            zones = [z for z in zones if z.active or not z.used][-2000:]

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, orders_placed, orders_filled)
    return trades_df, summary


def summarize(trades_df: pd.DataFrame, orders_placed: int, orders_filled: int) -> dict:
    if trades_df.empty:
        return {
            "trades": 0,
            "orders_placed": orders_placed,
            "orders_filled": orders_filled,
            "fill_rate": orders_filled / orders_placed if orders_placed else np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "expectancy_r": np.nan,
            "net_pnl_usd": 0.0,
            "max_drawdown_usd": 0.0,
            "avg_bars_held": np.nan,
        }
    wins = trades_df[trades_df["pnl_usd"] > 0]
    losses = trades_df[trades_df["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    equity = trades_df["pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    return {
        "trades": len(trades_df),
        "orders_placed": orders_placed,
        "orders_filled": orders_filled,
        "fill_rate": orders_filled / orders_placed if orders_placed else np.nan,
        "win_rate": len(wins) / len(trades_df),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": trades_df["r_multiple"].mean(),
        "net_pnl_usd": trades_df["pnl_usd"].sum(),
        "max_drawdown_usd": drawdown.min(),
        "avg_bars_held": trades_df["bars_held"].mean(),
    }
