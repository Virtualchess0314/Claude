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
    max_qty: int = 20
    use_session_close: bool = True
    session_tz: str = "America/New_York"
    session_close_hour: int = 16
    session_close_minute: int = 45


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

    zones: list[Zone] = []
    next_id = 1

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
            else:
                if h[i] >= pending_limit:
                    entry_price = max(pending_limit, o[i]) if o[i] >= pending_limit else pending_limit
                    entry_bar = i
                    state = "open"
                    orders_filled += 1

            if state == "waiting" and (not pending_zone.active or not within):
                state = "none"
                pending_zone = None

        # ── 4. Posición abierta: ¿tocó SL/TP o toca cierre de sesión? ───────
        if state == "open":
            exit_price = None
            reason = None
            if pending_is_long:
                hit_sl = l[i] <= pending_sl
                hit_tp = h[i] >= pending_tp
            else:
                hit_sl = h[i] >= pending_sl
                hit_tp = l[i] <= pending_tp

            if hit_sl:
                exit_price, reason = pending_sl, "SL"
            elif hit_tp:
                exit_price, reason = pending_tp, "TP"
            elif not within:
                exit_price, reason = c[i], "sesion"

            if exit_price is not None:
                direction = 1 if pending_is_long else -1
                pnl_usd = direction * (exit_price - entry_price) * pending_qty * p.point_value_usd
                risk_usd = p.sl_atr_mult * atr[entry_bar] * pending_qty * p.point_value_usd
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
                pending_zone = None

        # ── 5. Buscar una nueva zona elegible si estamos libres ─────────────
        if state == "none" and within and not np.isnan(a):
            tol = a * p.touch_tol_atr
            for z in zones:
                if z.active and z.flipped and not z.used:
                    is_long = not z.supply
                    limit_price = z.broken_boundary + tol if is_long else z.broken_boundary - tol
                    risk_r = p.sl_atr_mult * a
                    risk_usd_per_contract = risk_r * p.point_value_usd
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
                        if is_long:
                            pending_sl = limit_price - risk_r
                            pending_tp = limit_price + p.rr_target * risk_r
                        else:
                            pending_sl = limit_price + risk_r
                            pending_tp = limit_price - p.rr_target * risk_r
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
