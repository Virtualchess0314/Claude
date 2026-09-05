"""
Motor de backtest para UPF Artillery — réplica bar-a-bar de la lógica del
Pine Script (estrategias/upf_artillery/upf_artillery.pine), versión ya
corregida (TP escalonado con qty entera, sin solape sesión/EOD, reset
diario en la apertura de sesión).

Reglas replicadas:
  - ATR de Wilder, RSI de Wilder (misma fórmula que ta.atr / ta.rsi).
  - Pivotes altos/bajos no repintables: ta.pivothigh/low sólo confirma en
    la vela exacta piv_right barras después del extremo real.
  - Zona de demanda = [last_pl, last_pl + atr*sd_mult]; zona de oferta =
    [last_ph - atr*sd_mult, last_ph].
  - Entrada a MERCADO en el cierre de la vela de señal (mismo precio que
    se usa para calcular SL/TP1/TP2/TP3 — no hay desfase como en el IFVG
    original).
  - Salida en 3 etapas: 40% / 50% / resto de qty base, cada tramo con su
    propio TP y el mismo SL. Si el SL se toca, se cierra TODO lo que
    quede abierto (los 3 tramos comparten el mismo nivel de stop).
  - Máximo de operaciones/día y drawdown diario máximo (basado en equity
    intradía, incluye PnL flotante de la posición abierta) — el día de
    trading se resetea al ENTRAR a la sesión, no a medianoche.
  - Corte de sesión con cierre forzado de lo que quede abierto.
  - Comisión por contrato aplicada en CADA fill (entrada y cada tramo de
    salida) — así es como Pine aplica commission_type=cash_per_contract.
  - Slippage en ticks aplicado a TODOS los fills (entrada y salidas),
    siempre en contra — así es como Pine aplica el parámetro `slippage`
    de strategy(), a diferencia del IFVG donde sólo pegaba en salidas
    "de mercado".

Simplificación documentada: si en la misma vela se tocan el SL y algún
TP, se asume que el SL se ejecuta primero (conservador).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Params:
    piv_left: int = 3
    piv_right: int = 2

    atr_len: int = 14
    sd_mult: float = 1.0

    rsi_len: int = 14
    rsi_bull: float = 40.0
    rsi_bear: float = 60.0
    vol_len: int = 20
    vol_mult: float = 0.8

    base_qty: int = 10  # múltiplo de 10 para que 40%/50%/resto den enteros
    sl_mult: float = 1.5
    tp1_mult: float = 1.0
    tp2_mult: float = 2.0
    tp3_mult: float = 3.5

    sess_start: int = 930   # HHMM ET
    sess_end: int = 1555    # HHMM ET
    max_trades: int = 5
    max_dd_pct: float = 3.5

    initial_capital: float = 50000.0
    point_value_usd: float = 2.0
    tick_size: float = 0.25
    commission_per_contract: float = 0.62  # por fill, como en el .pine
    slippage_ticks: float = 1.0            # aplica a TODOS los fills


def _wilder_rma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) >= length:
        prev = values[:length].mean()
        out[length - 1] = prev
        for i in range(length, len(values)):
            prev = (prev * (length - 1) + values[i]) / length
            out[i] = prev
    return out


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> np.ndarray:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return _wilder_rma(tr.to_numpy(dtype=float), length)


def wilder_rsi(close: pd.Series, length: int) -> np.ndarray:
    delta = close.diff().to_numpy(dtype=float)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _wilder_rma(gain, length)
    avg_loss = _wilder_rma(loss, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
    rsi = np.where(avg_loss == 0, 100.0, rsi)
    rsi = np.where(np.isnan(avg_gain) | np.isnan(avg_loss), np.nan, rsi)
    return rsi


def pivots(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Réplica de ta.pivothigh/ta.pivotlow: devuelve, por barra, el valor del
    pivote si ESA barra es exactamente donde se confirma uno (right barras
    después del extremo real), o NaN si no.
    """
    n = len(high)
    window = left + right + 1
    piv_h = np.full(n, np.nan)
    piv_l = np.full(n, np.nan)
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


def _within_session(ct: int, p: Params) -> tuple[bool, bool]:
    in_sess = p.sess_start <= ct < p.sess_end
    is_eod = ct >= p.sess_end
    return in_sess, is_eod


def simulate(df: pd.DataFrame, p: Params, session_tz: str = "America/New_York") -> tuple[pd.DataFrame, dict]:
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)

    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    rsi = wilder_rsi(df["close"], p.rsi_len)
    piv_h, piv_l = pivots(h, l, p.piv_left, p.piv_right)

    if "volume" in df.columns:
        vol = df["volume"].to_numpy(dtype=float)
        vma = df["volume"].rolling(p.vol_len).mean().to_numpy()
        vol_ok_arr = vol >= vma * p.vol_mult
        volume_available = True
    else:
        vol_ok_arr = np.full(n, True)
        volume_available = False

    qty_t1 = round(p.base_qty * 0.40)
    qty_t2 = round(p.base_qty * 0.50)
    qty_t3 = p.base_qty - qty_t1 - qty_t2
    slip = p.slippage_ticks * p.tick_size

    last_ph = last_pl = np.nan
    was_in_sess = False
    dtrades = 0
    day_start_equity = p.initial_capital
    realized_equity = p.initial_capital

    # posición abierta
    is_long = False
    open_pos = False
    entry_price = entry_bar = None
    sl = tp1 = tp2 = tp3 = np.nan
    remaining = 0
    t1_done = t2_done = False
    fills = []  # fills parciales de la operación en curso (para armar el registro final)

    trades = []

    def commission(qty: int) -> float:
        return p.commission_per_contract * qty

    for i in range(n):
        local = ts[i].tz_convert(session_tz)
        ct = local.hour * 100 + local.minute
        in_sess, is_eod = _within_session(ct, p)

        entering_session = in_sess and not was_in_sess
        if entering_session:
            dtrades = 0
            day_start_equity = realized_equity + (
                (c[i] - entry_price) * remaining * p.point_value_usd * (1 if is_long else -1) if open_pos else 0.0
            )
        was_in_sess = in_sess

        if not np.isnan(piv_h[i]):
            last_ph = piv_h[i]
        if not np.isnan(piv_l[i]):
            last_pl = piv_l[i]

        # ── posición abierta: chequear fills (SL primero si se toca) ────
        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                pnl = (exit_price - entry_price) * remaining * p.point_value_usd * (1 if is_long else -1)
                pnl -= commission(remaining)
                fills.append(pnl)
                realized_equity += pnl
                trades.append(_close_trade(ts, entry_bar, i, is_long, entry_price, exit_price, p.base_qty, "SL", sum(fills), atr[entry_bar], p))
                open_pos = False
            else:
                levels = [(tp1, qty_t1, "t1"), (tp2, qty_t2, "t2"), (tp3, qty_t3, "t3")]
                hit = (lambda lvl: h[i] >= lvl) if is_long else (lambda lvl: l[i] <= lvl)

                for lvl, qty, tag in levels:
                    if tag == "t1" and t1_done:
                        continue
                    if tag == "t2" and t2_done:
                        continue
                    if hit(lvl):
                        exit_price = lvl - slip if is_long else lvl + slip
                        this_qty = qty if tag != "t3" else remaining
                        pnl = (exit_price - entry_price) * this_qty * p.point_value_usd * (1 if is_long else -1)
                        pnl -= commission(this_qty)
                        fills.append(pnl)
                        realized_equity += pnl
                        remaining -= this_qty
                        if tag == "t1":
                            t1_done = True
                        elif tag == "t2":
                            t2_done = True
                        if tag == "t3" or remaining <= 0:
                            trades.append(_close_trade(ts, entry_bar, i, is_long, entry_price, exit_price, p.base_qty, "TP", sum(fills), atr[entry_bar], p))
                            open_pos = False
                            break

            # cierre forzado de sesión
            if open_pos and is_eod:
                exit_price = c[i] - slip if is_long else c[i] + slip
                pnl = (exit_price - entry_price) * remaining * p.point_value_usd * (1 if is_long else -1)
                pnl -= commission(remaining)
                fills.append(pnl)
                realized_equity += pnl
                trades.append(_close_trade(ts, entry_bar, i, is_long, entry_price, exit_price, p.base_qty, "EOD", sum(fills), atr[entry_bar], p))
                open_pos = False

        # ── equity / drawdown diario ─────────────────────────────────────
        unreal_now = (
            (c[i] - entry_price) * remaining * p.point_value_usd * (1 if is_long else -1) if open_pos else 0.0
        )
        current_equity = realized_equity + unreal_now
        ddd = (day_start_equity - current_equity) / day_start_equity * 100 if day_start_equity > 0 else 0.0
        can_trade = dtrades < p.max_trades and ddd < p.max_dd_pct and in_sess

        # ── señales de entrada (sólo si está flat) ──────────────────────
        if not open_pos and not np.isnan(atr[i]) and not np.isnan(rsi[i]):
            demand_hi = last_pl + atr[i] * p.sd_mult if not np.isnan(last_pl) else np.nan
            supply_lo = last_ph - atr[i] * p.sd_mult if not np.isnan(last_ph) else np.nan

            long_go = (
                not np.isnan(piv_l[i]) and not np.isnan(demand_hi) and l[i] <= demand_hi
                and rsi[i] >= p.rsi_bull and c[i] > o[i] and vol_ok_arr[i] and can_trade
            )
            short_go = (
                not np.isnan(piv_h[i]) and not np.isnan(supply_lo) and h[i] >= supply_lo
                and rsi[i] <= p.rsi_bear and c[i] < o[i] and vol_ok_arr[i] and can_trade
            )

            if long_go or short_go:
                is_long = long_go
                entry_signal_price = c[i]
                entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
                entry_bar = i
                atr_i = atr[i]
                sl = entry_signal_price - atr_i * p.sl_mult if is_long else entry_signal_price + atr_i * p.sl_mult
                tp1 = entry_signal_price + atr_i * p.tp1_mult if is_long else entry_signal_price - atr_i * p.tp1_mult
                tp2 = entry_signal_price + atr_i * p.tp2_mult if is_long else entry_signal_price - atr_i * p.tp2_mult
                tp3 = entry_signal_price + atr_i * p.tp3_mult if is_long else entry_signal_price - atr_i * p.tp3_mult
                remaining = p.base_qty
                t1_done = t2_done = False
                fills = [-commission(p.base_qty)]  # comisión de entrada
                realized_equity += fills[0]
                dtrades += 1
                open_pos = True

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, p, volume_available)
    return trades_df, summary


def _close_trade(ts, entry_bar, exit_bar, is_long, entry_price, last_exit_price, base_qty, reason, net_pnl, entry_atr, p) -> dict:
    return {
        "entry_time": ts[entry_bar],
        "exit_time": ts[exit_bar],
        "direction": "long" if is_long else "short",
        "entry": entry_price,
        "reason": reason,
        "qty": base_qty,
        "pnl_usd": net_pnl,
        "r_multiple": net_pnl / (p.sl_mult * entry_atr * base_qty * p.point_value_usd) if entry_atr > 0 else np.nan,
        "bars_held": exit_bar - entry_bar,
    }


def summarize(trades_df: pd.DataFrame, p: Params, volume_available: bool) -> dict:
    base = {"volume_filter_active": volume_available}
    if trades_df.empty:
        return {**base, "trades": 0, "win_rate": np.nan, "profit_factor": np.nan, "expectancy_r": np.nan,
                "net_pnl_usd": 0.0, "max_drawdown_usd": 0.0, "avg_bars_held": np.nan}
    wins = trades_df[trades_df["pnl_usd"] > 0]
    losses = trades_df[trades_df["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    equity = trades_df["pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    return {
        **base,
        "trades": len(trades_df),
        "win_rate": len(wins) / len(trades_df),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": trades_df["r_multiple"].mean(),
        "net_pnl_usd": trades_df["pnl_usd"].sum(),
        "max_drawdown_usd": drawdown.min(),
        "avg_bars_held": trades_df["bars_held"].mean(),
    }
