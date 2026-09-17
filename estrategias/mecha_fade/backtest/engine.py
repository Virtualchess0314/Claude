"""
Motor de backtest para Mecha Fade — réplica bar-a-bar de la lógica de
estrategias/mecha_fade/mecha_fade.pine, para barrer parámetros sin
depender del Strategy Tester de TradingView.

Estrategia (según la describió el usuario): 3 indicadores -AlphaTrend,
Pivot y "DIY"- actúan cada uno como nivel de soporte/resistencia. Cuando
una vela mete una mecha por FUERA de un nivel pero CIERRA de nuevo
adentro (barrido de liquidez / falso quiebre), se entra a MERCADO en el
cierre de esa vela buscando el movimiento opuesto (fade).

⚠️ PENDIENTE DE CONFIRMAR — dos huecos que el usuario todavía no definió,
rellenados acá con un supuesto explícito para poder tener un motor
completo y testeable desde ya:

  1. Indicador "DIY": no existe una fórmula pública estándar con ese
     nombre. Se implementó como PLACEHOLDER = canal Donchian (máximo/
     mínimo de `diy_period` velas). `diy_upper_lower()` es la única
     función que hay que tocar para reemplazarlo por el indicador real
     -todo lo demás (confluencia, entradas, salidas) no depende de cómo
     se calcule ese nivel, sólo de que exista un valor por vela.
  2. Regla de salida: no especificada. Se usó SL más allá del extremo de
     la mecha que disparó la señal (+ buffer en ATR) y TP a un múltiplo R
     de ese riesgo -ambos parametrizables (`sl_buffer_atr`, `tp_r_mult`)
     para poder barrerlos en optimize.py.

Reglas replicadas 1:1 con el .pine:
  - ATR/RSI de Wilder (misma fórmula que ta.atr/ta.rsi).
  - AlphaTrend: fórmula pública estándar (ratchet sobre low-ATR*mult en
    régimen alcista según RSI/MFI>=50, sobre high+ATR*mult si no).
  - Pivotes altos/bajos no repintables (ta.pivothigh/low, confirman
    `piv_right` barras después del extremo real).
  - Confluencia configurable: `confluence_need` indicadores de 3 deben
    coincidir en la misma dirección en la misma vela.
  - Si en la misma vela hay señal long Y short (indicadores contradictorios),
    se descarta la vela -criterio conservador, no hay forma de saber cuál
    "ganaría" intrabar con datos OHLC.
  - Entrada a MERCADO en el cierre de la vela de señal (con slippage,
    porque es orden de mercado). SL es un stop (con slippage). TP es un
    límite (sin slippage).
  - Tamaño de posición ajustado para arriesgar siempre `max_risk_usd`
    (mismo criterio que ifvg_sniper), tope en `max_qty` contratos.
  - Corte de sesión con cierre forzado, tope opcional de operaciones/día.
  - Comisión round-turn por contrato (una vez por operación cerrada).

Simplificación documentada: si en la misma vela se tocan SL y TP, se
asume que el SL se ejecuta primero (conservador, igual que en los otros
dos backtesters del repo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Params:
    # AlphaTrend
    at_period: int = 14
    at_mult: float = 1.0
    at_use_volume: bool = False  # si no hay columna volume, se fuerza False igual

    # Pivot
    piv_left: int = 3
    piv_right: int = 2

    # DIY (placeholder Donchian -ver nota en el docstring del módulo)
    diy_period: int = 20

    # Confluencia: cuántos de los 3 indicadores deben coincidir (1..3)
    confluence_need: int = 1

    # Riesgo / salida
    atr_len: int = 14
    sl_buffer_atr: float = 0.10
    tp_r_mult: float = 1.5
    max_risk_usd: float = 150.0
    point_value_usd: float = 2.0
    max_qty: int = 40

    # Sesión / control diario
    use_session: bool = True
    session_tz: str = "America/New_York"
    session_close_hour: int = 16
    session_close_minute: int = 45
    max_trades_per_day: int = 999  # 999 = sin límite práctico

    # Costos reales
    commission_round_turn_usd: float = 3.50  # confirmado Tradovate/Tradeify
    slippage_ticks: float = 1.0
    tick_size: float = 0.25


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


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int) -> np.ndarray:
    """Money Flow Index como ta.mfi de Pine: suma rodante simple (no Wilder)."""
    tp = (high + low + close) / 3.0
    raw_flow = tp * volume
    tp_prev = tp.shift(1)
    pos_flow = raw_flow.where(tp > tp_prev, 0.0)
    neg_flow = raw_flow.where(tp < tp_prev, 0.0)
    pos_sum = pos_flow.rolling(length).sum()
    neg_sum = neg_flow.rolling(length).sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        mr = pos_sum / neg_sum
        out = 100 - 100 / (1 + mr)
    out = out.where(neg_sum != 0, 100.0)
    return out.to_numpy(dtype=float)


def alpha_trend(df: pd.DataFrame, p: Params, volume_available: bool) -> np.ndarray:
    high, low, close = df["high"], df["low"], df["close"]
    atr = wilder_atr(high, low, close, p.at_period)
    up_t = (low - atr * p.at_mult).to_numpy()
    down_t = (high + atr * p.at_mult).to_numpy()

    use_volume = p.at_use_volume and volume_available
    momentum = mfi(high, low, close, df["volume"], p.at_period) if use_volume else wilder_rsi(close, p.at_period)

    n = len(df)
    at = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(up_t[i]) or np.isnan(down_t[i]) or np.isnan(momentum[i]):
            continue
        prev = at[i - 1] if i > 0 else np.nan
        if momentum[i] >= 50:
            at[i] = max(up_t[i], prev) if not np.isnan(prev) else up_t[i]
        else:
            at[i] = min(down_t[i], prev) if not np.isnan(prev) else down_t[i]
    return at


def pivots(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Réplica de ta.pivothigh/ta.pivotlow (ver nota en engine.py de upf_artillery)."""
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


def diy_upper_lower(high: pd.Series, low: pd.Series, period: int) -> tuple[np.ndarray, np.ndarray]:
    """
    PLACEHOLDER del indicador "DIY" -no identificado. Canal Donchian
    (máximo/mínimo de `period` velas) sólo para tener un tercer nivel
    con el que probar el motor completo. Reemplazar por la fórmula real
    en cuanto se sepa cuál es; el resto del motor no depende de esto.
    """
    upper = high.rolling(period).max().to_numpy()
    lower = low.rolling(period).min().to_numpy()
    return upper, lower


def _within_session(ct_min: int, p: Params) -> tuple[bool, bool]:
    if not p.use_session:
        return True, False
    cutoff = p.session_close_hour * 60 + p.session_close_minute
    return ct_min < cutoff, ct_min >= cutoff


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    piv_h, piv_l = pivots(h, l, p.piv_left, p.piv_right)
    diy_upper, diy_lower = diy_upper_lower(df["high"], df["low"], p.diy_period)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)

    slip = p.slippage_ticks * p.tick_size

    last_ph = last_pl = np.nan
    last_day = None
    dtrades = 0

    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0

    trades = []

    for i in range(n):
        local = ts[i].tz_convert(p.session_tz)
        ct_min = local.hour * 60 + local.minute
        day_id = local.date()
        if day_id != last_day:
            dtrades = 0
            last_day = day_id
        in_sess, is_eod = _within_session(ct_min, p)

        if not np.isnan(piv_h[i]):
            last_ph = piv_h[i]
        if not np.isnan(piv_l[i]):
            last_pl = piv_l[i]

        # ── posición abierta: chequear SL/TP (SL primero si empatan) ────
        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)
            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
            elif hit_tp:
                exit_price = tp  # orden límite, sin slippage
                exit_reason = "TP"
            elif is_eod:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "EOD"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * qty
                risk_pts = abs(entry_price - sl)
                trades.append({
                    "entry_time": ts[entry_bar],
                    "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "entry": entry_price,
                    "exit": exit_price,
                    "reason": exit_reason,
                    "qty": qty,
                    "pnl_usd": pnl,
                    "r_multiple": pnl / (risk_pts * qty * p.point_value_usd) if risk_pts and risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        # ── señales de entrada (sólo si está flat) ──────────────────────
        if not open_pos:
            can_trade = dtrades < p.max_trades_per_day and in_sess and not np.isnan(atr_risk[i])

            long_at = not np.isnan(alpha[i]) and c[i] > alpha[i] and l[i] < alpha[i]
            short_at = not np.isnan(alpha[i]) and c[i] < alpha[i] and h[i] > alpha[i]

            long_piv = not np.isnan(last_pl) and l[i] < last_pl and c[i] > last_pl
            short_piv = not np.isnan(last_ph) and h[i] > last_ph and c[i] < last_ph

            long_diy = not np.isnan(diy_lower[i]) and l[i] < diy_lower[i] and c[i] > diy_lower[i]
            short_diy = not np.isnan(diy_upper[i]) and h[i] > diy_upper[i] and c[i] < diy_upper[i]

            n_long = int(long_at) + int(long_piv) + int(long_diy)
            n_short = int(short_at) + int(short_piv) + int(short_diy)

            long_go = can_trade and n_long >= p.confluence_need and n_short < p.confluence_need
            short_go = can_trade and n_short >= p.confluence_need and n_long < p.confluence_need

            if long_go or short_go:
                is_long = long_go
                entry_signal_price = c[i]
                entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
                entry_bar = i
                if is_long:
                    sl = l[i] - atr_risk[i] * p.sl_buffer_atr
                    risk_pts = entry_price - sl
                    tp = entry_price + risk_pts * p.tp_r_mult
                else:
                    sl = h[i] + atr_risk[i] * p.sl_buffer_atr
                    risk_pts = sl - entry_price
                    tp = entry_price - risk_pts * p.tp_r_mult
                qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
                if qty > 0:
                    open_pos = True
                    dtrades += 1

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, volume_available)
    return trades_df, summary


def summarize(trades_df: pd.DataFrame, volume_available: bool) -> dict:
    base = {"volume_available": volume_available}
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
