"""
Motor de backtest para IFVG Sniper — réplica bar-a-bar de la lógica del
Pine Script (estrategias/ifvg_sniper/ifvg_sniper.pine), para poder barrer
parámetros sin depender del Strategy Tester de TradingView.

Reglas replicadas 1:1 con el .pine:
  - ATR de Wilder (misma fórmula que ta.atr de Pine: semilla = SMA de los
    primeros N true ranges, luego suavizado RMA).
  - Detección de FVG de 3 velas + filtros de forma (cuerpo/rango de la vela
    de impulso).
  - Inversión (IFVG) cuando el cierre rompe la zona con buffer.
  - Expiración por edad o por re-rotura.
  - Entrada por ORDEN LÍMITE en el borde roto (no a mercado) — el pedido
    puesto en la vela i sólo puede llenarse desde la vela i+1 en adelante
    (para no meter look-ahead bias), igual que en la práctica: decidís la
    señal recién al cierre de la vela.
  - Tamaño de posición ajustado para arriesgar siempre `max_risk_usd`.
  - Corte de sesión (no overnight).

Simplificaciones respecto al Pine real (documentadas, no escondidas):
  - Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
    primero (supuesto conservador — no sabemos el orden real intrabar).
  - El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Params:
    max_ifvg_age: int = 60
    min_gap_atr: float = 0.75
    min_body_ratio: float = 0.5
    min_range_atr: float = 0.6
    clean_break_buffer_atr: float = 0.05
    touch_tol_atr: float = 0.05
    atr_len: int = 14
    sl_atr_mult: float = 1.0
    rr_target: float = 1.0
    max_risk_usd: float = 150.0
    point_value_usd: float = 2.0
    max_qty: int = 40  # límite real confirmado de la cuenta de 50k de Tradeify (micros)
    use_session_close: bool = True
    session_tz: str = "America/New_York"
    session_close_hour: int = 16
    session_close_minute: int = 45

    # filtro opcional de horario de entrada (no afecta salidas de posiciones
    # ya abiertas, sólo si se permite ABRIR una orden nueva). None = sin filtro.
    entry_start_hour: Optional[float] = None
    entry_end_hour: Optional[float] = None

    # filtro opcional de tendencia: sólo tomar longs con close > EMA(trend_ema_len)
    # y shorts con close < EMA(...). None = sin filtro (toma ambas direcciones
    # siempre). Idea: operar el retest IFVG sólo a favor de la tendencia mayor.
    trend_ema_len: Optional[int] = None

    # ── Costos reales ────────────────────────────────────────────────────
    # comisión "round-turn" (ida + vuelta) por contrato, en USD: comisión del
    # bróker/Tradovate + fee de CME + fee regulatorio NFA, todo junto.
    commission_round_turn_usd: float = 0.0
    # slippage en ticks, aplicado SOLO a salidas que en la vida real serían
    # orden de mercado/stop (SL y cierre forzado de sesión) — la entrada y el
    # TP son órdenes límite, no deberían sufrir slippage si el motor de la
    # cuenta las respeta como tales.
    slippage_ticks: float = 0.0
    tick_size: float = 0.25  # MNQ = 0.25 puntos de índice por tick

    # ── Breakeven-stop opcional ─────────────────────────────────────────
    # Cuando el precio recorre esta fracción del camino hacia el TP (medido
    # sobre el máximo favorable alcanzado, no sólo el cierre), el SL se
    # mueve a breakeven (+ buffer opcional) y ya no vuelve atrás. None =
    # desactivado (comportamiento original, SL fijo todo el trade).
    breakeven_trigger_frac: Optional[float] = None
    breakeven_buffer_ticks: float = 0.0

    # Cómo resolver una vela que toca SL y TP a la vez (no sabemos el orden
    # real intrabar con datos OHLC de 5m). "sl_first" es el supuesto
    # conservador de siempre; "tp_first" da la cota optimista — con un SL
    # tan cerca del precio como el breakeven, este empate se vuelve mucho
    # más frecuente, así que conviene mirar ambas cotas, no sólo una.
    tie_break: str = "sl_first"

    # Entrada "más profunda": en vez de poner la orden límite justo en el
    # nivel de retest, se pone entry_shift_frac × (sl_atr_mult×ATR) más
    # adentro de la zona (más cerca de donde iría el SL). El SL y el TP
    # quedan en el MISMO nivel de precio que hubieran tenido con la entrada
    # original (no se recalculan) — así que entrar más profundo reduce el
    # riesgo real y agranda la recompensa real de cualquier operación que
    # efectivamente llegue a ese precio, a costa de perderse las que van
    # directo al TP sin bajar tanto. 0.0 = comportamiento original.
    entry_shift_frac: float = 0.0

    # Scale-in: si el precio recorre scale_in_trigger_frac del camino hacia
    # el SL (sobre el peor punto alcanzado), se agregan scale_in_add_qty
    # contratos más al precio de ese momento, promediando el precio de
    # entrada. El SL y el TP originales no cambian. None = desactivado.
    scale_in_trigger_frac: Optional[float] = None
    scale_in_add_qty: int = 1


@dataclass
class Zone:
    id: int
    top: float
    bot: float
    supply: bool
    active: bool = True
    flipped: bool = False
    flip_bar: Optional[int] = None
    broken_boundary: Optional[float] = None
    used: bool = False


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
        con columnas open/high/low/close (nombres en minúscula).
    Devuelve (trades_df, resumen).
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    ts = df.index
    n = len(df)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len).values
    trend_ema = (
        df["close"].ewm(span=p.trend_ema_len, adjust=False).mean().values
        if p.trend_ema_len
        else None
    )

    zones: list[Zone] = []
    next_id = 1

    state = "none"  # none -> waiting -> open
    pending_zone: Optional[Zone] = None
    pending_is_long = False
    pending_limit = np.nan
    pending_sl = np.nan
    pending_tp = np.nan
    pending_qty = 0
    worst_since_entry = np.nan  # low más bajo (long) / high más alto (short) desde que abrió
    best_since_entry = np.nan   # high más alto (long) / low más bajo (short) desde que abrió
    moved_to_be = False
    scaled_in = False

    trades = []
    orders_placed = 0
    orders_filled = 0

    entry_price = entry_bar = None

    for i in range(n):
        a = atr[i]
        within = _within_session(ts[i], p)

        # ── 1. Detectar nuevos FVG (necesita al menos 3 velas) ──────────────
        if i >= 2 and not np.isnan(a):
            mid_range = h[i - 1] - l[i - 1]
            body_ratio = abs(c[i - 1] - o[i - 1]) / mid_range if mid_range > 0 else 0.0
            range_atr = mid_range / a if a > 0 else 0.0
            passes_shape = body_ratio >= p.min_body_ratio and range_atr >= p.min_range_atr

            if passes_shape:
                gap_up = l[i] - h[i - 2]
                if gap_up > 0 and a > 0 and gap_up / a >= p.min_gap_atr:
                    zones.append(Zone(next_id, l[i], h[i - 2], supply=False, active=True))
                    next_id += 1
                gap_dn = l[i - 2] - h[i]
                if gap_dn > 0 and a > 0 and gap_dn / a >= p.min_gap_atr:
                    zones.append(Zone(next_id, l[i - 2], h[i], supply=True, active=True))
                    next_id += 1

        # ── 2. Flip / expiración de zonas activas ───────────────────────────
        buf = a * p.clean_break_buffer_atr if not np.isnan(a) else 0.0
        for z in zones:
            if not z.active:
                continue
            if not z.flipped:
                filled = c[i] >= z.top + buf if z.supply else c[i] <= z.bot - buf
                if filled:
                    z.supply = not z.supply
                    z.flipped = True
                    z.flip_bar = i
                    z.broken_boundary = z.bot if z.supply else z.top
            else:
                expired = (i - z.flip_bar) > p.max_ifvg_age
                rebroken = c[i] >= z.top + buf if z.supply else c[i] <= z.bot - buf
                if expired or rebroken:
                    z.active = False

        # ── 3. Orden pendiente: ¿se llenó, se cancela? ──────────────────────
        if state == "waiting":
            if pending_is_long:
                if l[i] <= pending_limit:
                    entry_price = min(pending_limit, o[i]) if o[i] <= pending_limit else pending_limit
                    entry_bar = i
                    state = "open"
                    orders_filled += 1
                    worst_since_entry = l[i]
                    best_since_entry = h[i]
                    moved_to_be = False
                    scaled_in = False
            else:
                if h[i] >= pending_limit:
                    entry_price = max(pending_limit, o[i]) if o[i] >= pending_limit else pending_limit
                    entry_bar = i
                    state = "open"
                    orders_filled += 1
                    worst_since_entry = h[i]
                    best_since_entry = l[i]
                    moved_to_be = False
                    scaled_in = False

            if state == "waiting" and (not pending_zone.active or not within):
                state = "none"
                pending_zone = None

        # ── 4. Posición abierta: ¿tocó SL/TP o toca cierre de sesión? ───────
        if state == "open":
            if pending_is_long:
                worst_since_entry = min(worst_since_entry, l[i])
                best_since_entry = max(best_since_entry, h[i])
            else:
                worst_since_entry = max(worst_since_entry, h[i])
                best_since_entry = min(best_since_entry, l[i])

            if p.breakeven_trigger_frac is not None and not moved_to_be:
                reward_dist = abs(pending_tp - entry_price)
                progressed = abs(best_since_entry - entry_price)
                if reward_dist > 0 and progressed / reward_dist >= p.breakeven_trigger_frac:
                    buf = p.breakeven_buffer_ticks * p.tick_size
                    new_sl = entry_price + buf if pending_is_long else entry_price - buf
                    # sólo mover si es una mejora real (más cerca del precio, nunca más lejos)
                    better = new_sl > pending_sl if pending_is_long else new_sl < pending_sl
                    if better:
                        pending_sl = new_sl
                    moved_to_be = True

            if p.scale_in_trigger_frac is not None and not scaled_in:
                risk_dist_sofar = abs(entry_price - pending_sl)
                progressed_adverse = abs(worst_since_entry - entry_price)
                if risk_dist_sofar > 0 and progressed_adverse / risk_dist_sofar >= p.scale_in_trigger_frac:
                    add_price = (
                        entry_price - p.scale_in_trigger_frac * risk_dist_sofar
                        if pending_is_long
                        else entry_price + p.scale_in_trigger_frac * risk_dist_sofar
                    )
                    add_qty = p.scale_in_add_qty
                    new_total_qty = pending_qty + add_qty
                    entry_price = (entry_price * pending_qty + add_price * add_qty) / new_total_qty
                    pending_qty = new_total_qty
                    scaled_in = True

            exit_price = None
            reason = None
            if pending_is_long:
                hit_sl = l[i] <= pending_sl
                hit_tp = h[i] >= pending_tp
            else:
                hit_sl = h[i] >= pending_sl
                hit_tp = l[i] <= pending_tp

            slip = p.slippage_ticks * p.tick_size
            if hit_sl and hit_tp and p.tie_break == "tp_first":
                hit_sl = False  # cota optimista: en el empate, gana el TP
            if hit_sl:
                exit_price, reason = pending_sl, ("BE" if moved_to_be else "SL")
            elif hit_tp:
                exit_price, reason = pending_tp, "TP"
            elif not within:
                exit_price, reason = c[i], "sesion"

            if exit_price is not None:
                direction = 1 if pending_is_long else -1
                if reason in ("SL", "BE", "sesion"):
                    # salidas "de mercado" en la práctica: el slippage juega
                    # siempre en contra (peor precio del que apuntabas)
                    exit_price = exit_price - direction * slip

                gross_pnl_usd = direction * (exit_price - entry_price) * pending_qty * p.point_value_usd
                commission_usd = p.commission_round_turn_usd * pending_qty
                pnl_usd = gross_pnl_usd - commission_usd

                # riesgo REAL de esta operación puntual: distancia entrada->SL
                # tal cual se llenó (si hay entry_shift_frac > 0, es MENOR al
                # "de catálogo" sl_atr_mult*atr, porque se entró más profundo
                # con el mismo SL de siempre).
                risk_dist = abs(entry_price - pending_sl)
                risk_usd = risk_dist * pending_qty * p.point_value_usd

                # MAE/MFE: qué tan cerca llegó el precio del SL o del TP
                # contrarios ANTES de cerrar, sin importar cómo cerró al final
                # (aproximado con máximos/mínimos de vela, no con datos de tick).
                reward_dist = abs(pending_tp - entry_price)
                mae_dist = abs(entry_price - worst_since_entry)
                mfe_dist = abs(best_since_entry - entry_price)

                trades.append(
                    {
                        "entry_time": ts[entry_bar],
                        "exit_time": ts[i],
                        "direction": "long" if pending_is_long else "short",
                        "entry": entry_price,
                        "exit": exit_price,
                        "qty": pending_qty,
                        "reason": reason,
                        "gross_pnl_usd": gross_pnl_usd,
                        "commission_usd": commission_usd,
                        "pnl_usd": pnl_usd,
                        "r_multiple": pnl_usd / risk_usd if risk_usd > 0 else np.nan,
                        "bars_held": i - entry_bar,
                        "mae_frac": mae_dist / risk_dist if risk_dist > 0 else np.nan,
                        "mfe_frac": mfe_dist / reward_dist if reward_dist > 0 else np.nan,
                        "scaled_in": scaled_in,
                    }
                )
                state = "none"
                pending_zone = None

        # ── 5. Buscar una nueva zona elegible si estamos libres ─────────────
        entry_hour_ok = True
        if p.entry_start_hour is not None:
            local = ts[i].tz_convert(p.session_tz)
            hour_now = local.hour + local.minute / 60
            if p.entry_start_hour <= p.entry_end_hour:
                entry_hour_ok = p.entry_start_hour <= hour_now < p.entry_end_hour
            else:  # ventana que cruza medianoche
                entry_hour_ok = hour_now >= p.entry_start_hour or hour_now < p.entry_end_hour

        if state == "none" and within and entry_hour_ok and not np.isnan(a):
            tol = a * p.touch_tol_atr
            for z in zones:
                if z.active and z.flipped and not z.used:
                    is_long = not z.supply
                    if trend_ema is not None:
                        aligned = (c[i] > trend_ema[i]) if is_long else (c[i] < trend_ema[i])
                        if not aligned:
                            continue
                    # nivel de confirmación "de catálogo" (comportamiento original)
                    catalog_entry = z.broken_boundary + tol if is_long else z.broken_boundary - tol
                    risk_r = p.sl_atr_mult * a
                    sl_level = catalog_entry - risk_r if is_long else catalog_entry + risk_r
                    tp_level = catalog_entry + p.rr_target * risk_r if is_long else catalog_entry - p.rr_target * risk_r

                    # entrada real: opcionalmente más profunda, SL/TP fijos
                    shift = p.entry_shift_frac * risk_r
                    limit_price = catalog_entry - shift if is_long else catalog_entry + shift
                    actual_risk_r = abs(limit_price - sl_level)

                    risk_usd_per_contract = actual_risk_r * p.point_value_usd
                    qty = (
                        int(min(p.max_qty, np.floor(p.max_risk_usd / risk_usd_per_contract)))
                        if risk_usd_per_contract > 0
                        else 0
                    )
                    if qty >= 1:
                        z.used = True
                        pending_zone = z
                        pending_is_long = is_long
                        pending_limit = limit_price
                        pending_qty = qty
                        pending_sl = sl_level
                        pending_tp = tp_level
                        state = "waiting"
                        orders_placed += 1
                        break

        # limpieza de memoria (paridad con el .pine, sin efecto en las señales)
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
