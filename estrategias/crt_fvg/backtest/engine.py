"""
Motor de backtest para CRT + FVG — Candle Range Theory (concepto ICT) con
la entrada afinada por Fair Value Gap en un timeframe menor.

Mecánica (multi-timeframe):
  1. HTF (timeframe mayor, ej. 1h): dos velas consecutivas, C1 y C2.
     - Si C2 rompe el máximo de C1 y CIERRA de vuelta por debajo de ese
       máximo → barrido bajista confirmado. Bias = SHORT. Objetivo (TP) =
       mínimo de C1 (el extremo opuesto del rango). SL de referencia =
       máximo de C2 (la mecha del barrido).
     - Simétrico para el mínimo: barrido alcista → bias LONG, TP = máximo
       de C1, SL de referencia = mínimo de C2.
  2. Una vez que C2 CIERRA (no antes — sin look-ahead), se abre una
     "ventana de confirmación" de `max_wait_htf_bars` velas HTF.
  3. Dentro de esa ventana, en el timeframe MENOR (LTF, ej. 15m), se
     busca el primer FVG que aparezca EN LA MISMA DIRECCIÓN del bias
     (FVG alcista si bias=LONG, bajista si bias=SHORT) — funciona como
     proxy del "cambio de estructura + FVG" que se usa en la práctica
     para afinar la entrada. Se opera el retest de ESE FVG.
  4. Entrada por ORDEN LÍMITE en el borde del FVG (igual que
     ifvg_sniper/fvg_continuation). SL = extremo de C2 (el barrido) +
     buffer. TP = FIJO, el extremo opuesto de C1 (no un R:R ni un swing
     buscado — así lo define CRT).
  5. Una operación a la vez; si no aparece ningún FVG en la dirección
     correcta dentro de la ventana, ese setup se descarta sin operar.

Se reutiliza la detección de FVG y los filtros de forma de
ifvg_sniper/fvg_continuation (ATR de Wilder, cuerpo/rango de la vela de
impulso, `min_gap_atr`), y el mismo esquema de orden límite con relleno
sin look-ahead (la señal se decide al cierre de la vela LTF, se llena
desde la vela siguiente en adelante).

Simplificaciones (documentadas, no escondidas):
  - El HTF se deriva por resample del propio CSV del LTF (ver
    data.resample_ohlc) — sólo válido si `htf_rule` es múltiplo exacto
    del timeframe del CSV cargado.
  - No se implementa un detector formal de "market structure shift"
    (MSS) de swings — el FVG en la dirección del bias hace de proxy de
    esa confirmación, tal como sugieren varias guías de CRT ("ejecutar
    en el retest del nivel desplazado o un FVG chico").
  - Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
    primero (supuesto conservador, igual que en el resto del repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from data import resample_ohlc


@dataclass
class Params:
    htf_rule: str = "1h"
    max_wait_htf_bars: int = 2  # cuántas velas HTF, después del cierre de C2, se espera el FVG de entrada

    min_gap_atr: float = 0.5
    min_body_ratio: float = 0.5
    min_range_atr: float = 0.6
    touch_tol_atr: float = 0.05
    sl_buffer_atr: float = 0.05
    atr_len: int = 14
    min_rr: float = 0.0  # descarta el setup si el TP fijo (rango de C1) da menos de este R:R contra el SL (mecha de C2)

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

    tie_break: str = "sl_first"


@dataclass
class BiasEvent:
    ready_time: pd.Timestamp   # cuándo cierra C2 (desde acá se busca el FVG)
    expire_time: pd.Timestamp
    is_long: bool
    target: float              # TP fijo = extremo opuesto de C1
    sl_ref: float              # extremo de C2 (la mecha del barrido)
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


def build_bias_events(df_ltf: pd.DataFrame, p: Params) -> list[BiasEvent]:
    """Detecta los barridos C1->C2 en el HTF (derivado del LTF) y arma la
    lista de eventos de bias con su ventana de confirmación."""
    htf = resample_ohlc(df_ltf, p.htf_rule)
    h, l, c = htf["high"].to_numpy(), htf["low"].to_numpy(), htf["close"].to_numpy()
    htf_delta = pd.Timedelta(p.htf_rule)

    events: list[BiasEvent] = []
    for H in range(1, len(htf)):
        sweep_high = h[H] > h[H - 1] and c[H] <= h[H - 1]
        sweep_low = l[H] < l[H - 1] and c[H] >= l[H - 1]
        if sweep_high and not sweep_low:
            ready = htf.index[H] + htf_delta
            events.append(BiasEvent(
                ready_time=ready,
                expire_time=ready + p.max_wait_htf_bars * htf_delta,
                is_long=False,
                target=l[H - 1],
                sl_ref=h[H],
            ))
        elif sweep_low and not sweep_high:
            ready = htf.index[H] + htf_delta
            events.append(BiasEvent(
                ready_time=ready,
                expire_time=ready + p.max_wait_htf_bars * htf_delta,
                is_long=True,
                target=h[H - 1],
                sl_ref=l[H],
            ))
    events.sort(key=lambda e: e.ready_time)
    return events


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """
    df: velas del timeframe MENOR (LTF) — el HTF se deriva acá adentro
        por resample. Índice datetime tz-aware, ascendente.
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    ts = df.index
    n = len(df)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len).values

    events = build_bias_events(df, p)
    event_idx = 0
    active_bias: Optional[BiasEvent] = None

    state = "none"  # none -> waiting -> open
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

        # ── 1. Activar el próximo bias HTF que ya esté listo ────────────────
        while event_idx < len(events) and events[event_idx].ready_time <= ts[i]:
            active_bias = events[event_idx]
            event_idx += 1
        if active_bias is not None and ts[i] >= active_bias.expire_time:
            active_bias = None

        # ── 2. Detectar FVG nuevo en esta vela (necesita al menos 3 velas) ──
        new_zone_is_long = None
        new_zone_top = new_zone_bot = np.nan
        if i >= 2 and not np.isnan(a):
            mid_range = h[i - 1] - l[i - 1]
            body_ratio = abs(c[i - 1] - o[i - 1]) / mid_range if mid_range > 0 else 0.0
            range_atr = mid_range / a if a > 0 else 0.0
            passes_shape = body_ratio >= p.min_body_ratio and range_atr >= p.min_range_atr

            if passes_shape:
                gap_up = l[i] - h[i - 2]
                gap_dn = l[i - 2] - h[i]
                if gap_up > 0 and a > 0 and gap_up / a >= p.min_gap_atr:
                    new_zone_is_long, new_zone_top, new_zone_bot = True, l[i], h[i - 2]
                elif gap_dn > 0 and a > 0 and gap_dn / a >= p.min_gap_atr:
                    new_zone_is_long, new_zone_top, new_zone_bot = False, l[i - 2], h[i]

        # ── 3. Orden pendiente: ¿se llenó, se cancela? ──────────────────────
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
            if state == "waiting" and not within:
                state = "none"

        # ── 4. Posición abierta: ¿tocó SL/TP o cierre de sesión? ────────────
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

        # ── 5. ¿Hay un FVG nuevo que confirme el bias activo? ───────────────
        entry_hour_ok = True
        if p.entry_start_hour is not None:
            local = ts[i].tz_convert(p.session_tz)
            hour_now = local.hour + local.minute / 60
            if p.entry_start_hour <= p.entry_end_hour:
                entry_hour_ok = p.entry_start_hour <= hour_now < p.entry_end_hour
            else:
                entry_hour_ok = hour_now >= p.entry_start_hour or hour_now < p.entry_end_hour

        if (
            state == "none"
            and within
            and entry_hour_ok
            and active_bias is not None
            and not active_bias.used
            and new_zone_is_long is not None
            and new_zone_is_long == active_bias.is_long
            and not np.isnan(a)
        ):
            tol = a * p.touch_tol_atr
            sl_buf = a * p.sl_buffer_atr
            is_long = active_bias.is_long
            entry_level = new_zone_top - tol if is_long else new_zone_bot + tol
            sl_level = active_bias.sl_ref - sl_buf if is_long else active_bias.sl_ref + sl_buf
            tp_level = active_bias.target
            risk_dist = abs(entry_level - sl_level)
            reward_dist = abs(tp_level - entry_level)
            rr_ok = risk_dist > 0 and (reward_dist / risk_dist) >= p.min_rr

            if risk_dist > 0 and rr_ok:
                risk_usd_per_contract = risk_dist * p.point_value_usd
                qty = (
                    int(min(p.max_qty, np.floor(p.max_risk_usd / risk_usd_per_contract)))
                    if risk_usd_per_contract > 0
                    else 0
                )
                if qty >= 1:
                    active_bias.used = True
                    pending_is_long = is_long
                    pending_limit = entry_level
                    pending_qty = qty
                    pending_sl = sl_level
                    pending_tp = tp_level
                    state = "waiting"
                    orders_placed += 1

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


@dataclass
class HtfOnlyParams:
    """Config para `simulate_htf_only`: prueba la mecánica CRT pura (sin
    la refinación por FVG en un timeframe menor), pensada para correr
    directo sobre datos NATIVOS de un solo timeframe (ej. 4h) con mucho
    más historial del que tenemos en 15m/5m."""

    sl_buffer_atr: float = 0.05
    atr_len: int = 14
    min_rr: float = 0.0

    max_risk_usd: float = 300.0
    point_value_usd: float = 2.0
    max_qty: int = 40

    commission_round_turn_usd: float = 0.0
    slippage_ticks: float = 0.0
    tick_size: float = 0.25
    tie_break: str = "sl_first"


def simulate_htf_only(df: pd.DataFrame, p: HtfOnlyParams) -> tuple[pd.DataFrame, dict]:
    """
    Versión SIN la capa de FVG en timeframe menor: en cuanto el barrido
    C1->C2 se confirma (C2 cierra), se entra a MERCADO en el open de la
    vela siguiente (C3) — tal como describe CRT clásico ("enter at the
    open of C3"). SL = mecha de C2 + buffer, TP = extremo opuesto de C1
    (fijo). Una operación a la vez, sin look-ahead: la señal se decide
    al cierre de C2, se opera recién en la vela siguiente.
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len).values
    ts = df.index

    state = "none"  # none -> pending (entra en el open de esta vela) -> open
    pending_is_long = False
    pending_sl = np.nan
    pending_tp = np.nan
    entry_price = entry_bar = None
    pending_qty = 0

    trades = []
    orders_placed = 0
    orders_filled = 0

    for i in range(n):
        if state == "pending":
            entry_price = o[i]
            entry_bar = i
            state = "open"
            orders_filled += 1

        if state == "open":
            if pending_is_long:
                hit_sl, hit_tp = l[i] <= pending_sl, h[i] >= pending_tp
            else:
                hit_sl, hit_tp = h[i] >= pending_sl, l[i] <= pending_tp

            exit_price = reason = None
            slip = p.slippage_ticks * p.tick_size
            if hit_sl and hit_tp and p.tie_break == "tp_first":
                hit_sl = False
            if hit_sl:
                exit_price, reason = pending_sl, "SL"
            elif hit_tp:
                exit_price, reason = pending_tp, "TP"

            if exit_price is not None:
                direction = 1 if pending_is_long else -1
                if reason == "SL":
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
                        "qty": pending_qty,
                        "reason": reason,
                        "pnl_usd": pnl_usd,
                        "r_multiple": pnl_usd / risk_usd if risk_usd > 0 else np.nan,
                        "bars_held": i - entry_bar,
                    }
                )
                state = "none"

        if state == "none" and i >= 1 and not np.isnan(atr[i]):
            sweep_high = h[i] > h[i - 1] and c[i] <= h[i - 1]
            sweep_low = l[i] < l[i - 1] and c[i] >= l[i - 1]
            is_long = None
            if sweep_high and not sweep_low:
                is_long = False
                target = l[i - 1]
                sl_ref = h[i]
            elif sweep_low and not sweep_high:
                is_long = True
                target = h[i - 1]
                sl_ref = l[i]

            if is_long is not None:
                buf = atr[i] * p.sl_buffer_atr
                sl_level = sl_ref - buf if is_long else sl_ref + buf
                # entrada aproximada = cierre de C2 (todavía no sabemos el open de C3)
                risk_dist_approx = abs(c[i] - sl_level)
                reward_dist_approx = abs(target - c[i])
                rr_ok = risk_dist_approx > 0 and (reward_dist_approx / risk_dist_approx) >= p.min_rr
                if risk_dist_approx > 0 and rr_ok:
                    risk_usd_per_contract = risk_dist_approx * p.point_value_usd
                    qty = (
                        int(min(p.max_qty, np.floor(p.max_risk_usd / risk_usd_per_contract)))
                        if risk_usd_per_contract > 0
                        else 0
                    )
                    if qty >= 1:
                        pending_is_long = is_long
                        pending_sl = sl_level
                        pending_tp = target
                        pending_qty = qty
                        state = "pending"
                        orders_placed += 1

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, orders_placed, orders_filled)
    return trades_df, summary
