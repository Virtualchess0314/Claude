"""
Motor de backtest para Hull Suite — réplica bar-a-bar de la lógica del Pine
Script (estrategias/hull_suite/hull_suite.pine), para poder barrer el
período (`length`) y la variante (Hma/Ehma/Thma) sin depender del Strategy
Tester de TradingView.

Qué hace el .pine, resumido:
  - Calcula una Hull MA (HMA, EHMA o THMA) de `length` períodos sobre
    `close`.
  - Señal = comparar el valor actual (HULL[0]) contra el de 2 velas atrás
    (HULL[2]): si es mayor, entra/queda long; si es menor, entra/queda
    short. No hay SL/TP — es un sistema "siempre adentro" (stop-and-reverse)
    con tamaño de posición = 100% del equity (percent_of_equity).
  - `strat_dir_input` restringe la dirección: en modo "long" o "short",
    `strategy.risk.allow_entry_in` BLOQUEA por completo las órdenes del
    lado contrario — no es que cierre y quede afuera, la posición abierta
    simplemente nunca se cierra (no hay ninguna otra orden que la
    cancele). Este motor replica ese comportamiento tal cual: en modo
    "long"/"short", después de la primera señal a favor, la posición no
    se vuelve a tocar nunca más, sin importar cuántas señales contrarias
    aparezcan después.

Simplificaciones respecto al Pine real (documentadas, no escondidas):
  - Fill a precio de CIERRE de la vela siguiente a la señal (close-to-close),
    no al open de la vela siguiente como haría de verdad TradingView al
    llenar la orden de mercado. Evita necesitar datos de open con
    granularidad intrabar y es el enfoque estándar en backtests
    vectorizados de sistemas "siempre adentro"; la diferencia práctica es
    chica salvo en instrumentos con gaps grandes entre cierre y apertura.
  - Los períodos fraccionarios que produce el Pine original al dividir
    `length` (ej. `length/2`, `round(sqrt(length))`) se manejan con
    truncamiento hacia cero para los `wma`/`ema` internos — la misma
    conversión implícita float→int que hace Pine al pasar esos valores a
    `ta.wma`/`ta.ema`. Con longitudes chicas (<10) esto puede desviarse
    un poco del resultado exacto de TradingView; confirmá siempre la
    combinación final en el Strategy Tester antes de operarla en real.
  - Comisión: se cobra `commission_pct` en CADA orden (abrir y cerrar son
    dos órdenes separadas, igual que hace Pine con
    `commission_type=percent`). No se modela slippage/spread.

Además de `simulate()` (fiel al .pine, sin SL/TP), este módulo agrega
`simulate_atr_stops()`: una VARIANTE con gestión de riesgo fija por ATR
(SL = sl_atr_mult × ATR, TP = rr_target × riesgo), pensada para evaluar si
el cruce de la Hull MA sirve como gatillo de entrada de una estrategia con
salidas administradas, en vez de un sistema siempre-adentro. No es parte
del `.pine` original — es un agregado para poder responder "¿qué ATR de
SL y qué R:R (1:1 / 1:1.5 / 1:2) le conviene a esta señal?".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Params:
    length: int = 55
    mode: str = "Hma"  # "Hma" | "Ehma" | "Thma"
    direction: str = "all"  # "long" | "short" | "all" (ver strat_dir_input del .pine)

    commission_pct: float = 0.0  # % por orden (igual a commission_value del .pine)
    initial_capital: float = 1000.0  # sólo para reportar PnL en moneda, no afecta métricas en %


def _trunc(x: float) -> int:
    """Conversión implícita float→int de Pine al pasar longitudes fraccionarias
    a funciones built-in (ta.wma/ta.ema): trunca hacia cero, mínimo 1."""
    return max(1, int(x))


def wma(src: pd.Series, length: float) -> pd.Series:
    n = _trunc(length)
    weights = np.arange(1, n + 1, dtype=float)
    return src.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def ema(src: pd.Series, length: float) -> pd.Series:
    n = _trunc(length)
    return src.ewm(span=n, adjust=False).mean()


def hma(src: pd.Series, length: float) -> pd.Series:
    sqrt_len = round(math.sqrt(length))
    return wma(2 * wma(src, length / 2) - wma(src, length), sqrt_len)


def ehma(src: pd.Series, length: float) -> pd.Series:
    sqrt_len = round(math.sqrt(length))
    return ema(2 * ema(src, length / 2) - ema(src, length), sqrt_len)


def thma(src: pd.Series, length: float) -> pd.Series:
    # Mode() llama THMA(src, length/2): acá `length` ya viene dividido a la mitad.
    return wma(wma(src, length / 3) * 3 - wma(src, length / 2) - wma(src, length), length)


def hull_ma(src: pd.Series, length: int, mode: str) -> pd.Series:
    if mode == "Hma":
        return hma(src, length)
    if mode == "Ehma":
        return ehma(src, length)
    if mode == "Thma":
        return thma(src, length / 2)
    raise ValueError(f"Modo desconocido: {mode!r} (esperado Hma/Ehma/Thma)")


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


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """
    df: DataFrame indexado por datetime (ordenado ascendente), con columna
        `close` (open/high/low se ignoran, la estrategia sólo usa close).
    Devuelve (trades_df, resumen).
    """
    close = df["close"]
    hull = hull_ma(close, p.length, p.mode)
    bull = hull > hull.shift(2)
    bear = hull < hull.shift(2)

    n = len(df)
    signal = np.zeros(n)  # +1 long, -1 short, 0 = sin señal nueva esa vela
    signal[bull.to_numpy(dtype=bool, na_value=False)] = 1
    signal[bear.to_numpy(dtype=bool, na_value=False)] = -1

    if p.direction == "long":
        signal[signal < 0] = 0
    elif p.direction == "short":
        signal[signal > 0] = 0
    elif p.direction != "all":
        raise ValueError(f"direction inválida: {p.direction!r} (esperado long/short/all)")

    # posición vigente durante la vela i = la última señal != 0 vista HASTA
    # la vela i-1 (fill a cierre de la vela siguiente a la señal, ver docstring).
    position = np.zeros(n)
    last = 0.0
    for i in range(n):
        position[i] = last
        if signal[i] != 0:
            last = signal[i]

    close_vals = close.to_numpy(dtype=float)
    bar_ret = np.zeros(n)
    bar_ret[1:] = close_vals[1:] / close_vals[:-1] - 1.0
    strat_ret = position * bar_ret  # PnL bruto (sin comisión) de la vela i

    commission = p.commission_pct / 100.0
    equity = np.full(n, np.nan, dtype=float)
    eq = p.initial_capital
    trades = []
    trade_start_i: Optional[int] = None
    trade_dir = 0.0
    trade_equity_start = eq

    for i in range(n):
        if position[i] != 0:
            eq *= 1.0 + strat_ret[i]

        prev_pos = position[i - 1] if i > 0 else 0.0
        # cierre de la operación anterior (cambio de posición o fin de datos)
        if trade_start_i is not None and position[i] != trade_dir:
            eq *= 1.0 - commission  # orden de cierre
            trades.append(
                {
                    "entry_time": df.index[trade_start_i],
                    "exit_time": df.index[i - 1] if i > 0 else df.index[trade_start_i],
                    "direction": "long" if trade_dir > 0 else "short",
                    "bars_held": (i - 1) - trade_start_i + 1,
                    "return_pct": (eq / trade_equity_start - 1.0) * 100.0,
                    "pnl": eq - trade_equity_start,
                }
            )
            trade_start_i = None

        # apertura de una operación nueva
        if position[i] != 0 and position[i] != prev_pos and trade_start_i is None:
            eq *= 1.0 - commission  # orden de apertura
            trade_start_i = i
            trade_dir = position[i]
            trade_equity_start = eq

        equity[i] = eq

    # operación que sigue abierta al final del historial: se reporta
    # mark-to-market, sin comisión de cierre (no se cerró de verdad).
    if trade_start_i is not None:
        trades.append(
            {
                "entry_time": df.index[trade_start_i],
                "exit_time": df.index[-1],
                "direction": "long" if trade_dir > 0 else "short",
                "bars_held": (n - 1) - trade_start_i + 1,
                "return_pct": (eq / trade_equity_start - 1.0) * 100.0,
                "pnl": eq - trade_equity_start,
                "open_at_end": True,
            }
        )

    trades_df = pd.DataFrame(trades)
    equity_s = pd.Series(equity, index=df.index)
    buy_hold_pct = (close_vals[-1] / close_vals[0] - 1.0) * 100.0
    summary = summarize(trades_df, equity_s, p.initial_capital, buy_hold_pct)
    return trades_df, summary


def summarize(trades_df: pd.DataFrame, equity: pd.Series, initial_capital: float, buy_hold_pct: float) -> dict:
    equity = equity.dropna()
    net_pct = (equity.iloc[-1] / initial_capital - 1.0) * 100.0 if len(equity) else 0.0
    running_max = equity.cummax()
    drawdown_pct = (equity / running_max - 1.0) * 100.0
    max_dd_pct = drawdown_pct.min() if len(drawdown_pct) else 0.0

    if trades_df.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "avg_trade_pct": np.nan,
            "net_return_pct": net_pct,
            "buy_hold_pct": buy_hold_pct,
            "max_drawdown_pct": max_dd_pct,
            "avg_bars_held": np.nan,
        }

    if "open_at_end" in trades_df.columns:
        is_open = trades_df["open_at_end"].fillna(False).astype(bool)
        closed = trades_df[~is_open]
    else:
        closed = trades_df
    wins = closed[closed["pnl"] > 0]
    losses = closed[closed["pnl"] <= 0]
    gross_profit = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    return {
        "trades": len(trades_df),
        "win_rate": len(wins) / len(closed) if len(closed) else np.nan,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "avg_trade_pct": closed["return_pct"].mean() if len(closed) else np.nan,
        "net_return_pct": net_pct,
        "buy_hold_pct": buy_hold_pct,
        "max_drawdown_pct": max_dd_pct,
        "avg_bars_held": trades_df["bars_held"].mean(),
    }


@dataclass
class StopParams:
    length: int = 55
    mode: str = "Hma"  # "Hma" | "Ehma" | "Thma"
    direction: str = "all"  # "long" | "short" | "all" — filtra qué cruces se toman como entrada

    atr_len: int = 14
    sl_atr_mult: float = 1.0  # SL = entrada ± sl_atr_mult × ATR
    rr_target: float = 1.0  # TP = entrada ± rr_target × riesgo (1.0 / 1.5 / 2.0 → R:R 1:1, 1:1.5, 1:2)

    risk_pct: float = 1.0  # % del equity arriesgado por operación (position sizing)
    commission_pct: float = 0.0  # % por orden, sobre el equity arriesgado
    initial_capital: float = 1000.0


def simulate_atr_stops(df: pd.DataFrame, sp: StopParams) -> tuple[pd.DataFrame, dict]:
    """
    Variante de `simulate()` con SL/TP fijos por ATR en vez de estar siempre
    adentro del mercado (ver docstring del módulo). Una operación a la vez:
    mientras hay una posición abierta, se ignoran nuevas señales hasta que
    el SL o el TP la cierren (igual que otros motores de este repo, ver
    ifvg_sniper/backtest/engine.py).

    Timing (sin look-ahead): la tendencia de Hull se evalúa con el CIERRE de
    la vela i (necesita HULL[i] vs HULL[i-2]); si en la vela i-1 se confirmó
    una tendencia nueva (cambio de signo respecto a la vela anterior) y hay
    lugar para entrar, la orden se llena al OPEN de la vela i — recién ahí
    se conoce el ATR fijado (el de la vela i-1, última vela cerrada).

    Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
    primero (supuesto conservador, igual que en ifvg_sniper/upf_artillery).
    """
    o, h, l, c = df["open"].to_numpy(dtype=float), df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float)
    ts = df.index
    n = len(df)

    hull = hull_ma(df["close"], sp.length, sp.mode).to_numpy(dtype=float)
    atr = wilder_atr(df["high"], df["low"], df["close"], sp.atr_len).to_numpy(dtype=float)

    trend = np.zeros(n)
    last = 0.0
    for i in range(n):
        if i >= 2 and not np.isnan(hull[i]) and not np.isnan(hull[i - 2]):
            if hull[i] > hull[i - 2]:
                last = 1.0
            elif hull[i] < hull[i - 2]:
                last = -1.0
        trend[i] = last

    commission = sp.commission_pct / 100.0
    risk_frac = sp.risk_pct / 100.0

    state = "flat"
    direction = 0.0
    entry_price = entry_bar = sl = tp = None
    trades = []
    equity = np.full(n, np.nan, dtype=float)
    eq = sp.initial_capital

    for i in range(n):
        if state == "flat" and i >= 2:
            prev_trend = trend[i - 1]
            prev_prev_trend = trend[i - 2]
            is_new_trend = prev_trend != 0 and prev_trend != prev_prev_trend
            allowed = (
                sp.direction == "all"
                or (sp.direction == "long" and prev_trend > 0)
                or (sp.direction == "short" and prev_trend < 0)
            )
            a = atr[i - 1]
            if is_new_trend and allowed and not np.isnan(a) and a > 0:
                direction = prev_trend
                entry_price = o[i]
                entry_bar = i
                risk_dist = sp.sl_atr_mult * a
                sl = entry_price - direction * risk_dist
                tp = entry_price + direction * sp.rr_target * risk_dist
                eq *= 1.0 - commission
                state = "open"

        if state == "open":
            if direction > 0:
                hit_sl, hit_tp = l[i] <= sl, h[i] >= tp
            else:
                hit_sl, hit_tp = h[i] >= sl, l[i] <= tp

            exit_price = reason = None
            if hit_sl:
                exit_price, reason = sl, "SL"
            elif hit_tp:
                exit_price, reason = tp, "TP"

            if exit_price is not None:
                r_multiple = sp.rr_target if reason == "TP" else -1.0
                eq *= 1.0 + risk_frac * r_multiple
                eq *= 1.0 - commission
                trades.append(
                    {
                        "entry_time": ts[entry_bar],
                        "exit_time": ts[i],
                        "direction": "long" if direction > 0 else "short",
                        "entry": entry_price,
                        "exit": exit_price,
                        "reason": reason,
                        "r_multiple": r_multiple,
                        "bars_held": i - entry_bar,
                    }
                )
                state = "flat"

        equity[i] = eq

    if state == "open":
        direction_sign = direction
        mtm_r = direction_sign * (c[-1] - entry_price) / (sp.sl_atr_mult * atr[entry_bar - 1] if entry_bar > 0 else np.nan)
        trades.append(
            {
                "entry_time": ts[entry_bar],
                "exit_time": ts[-1],
                "direction": "long" if direction > 0 else "short",
                "entry": entry_price,
                "exit": c[-1],
                "reason": "abierta_al_final",
                "r_multiple": mtm_r,
                "bars_held": (n - 1) - entry_bar,
                "open_at_end": True,
            }
        )

    trades_df = pd.DataFrame(trades)
    equity_s = pd.Series(equity, index=df.index)
    buy_hold_pct = (c[-1] / c[0] - 1.0) * 100.0
    summary = summarize_atr_stops(trades_df, equity_s, sp.initial_capital, buy_hold_pct)
    return trades_df, summary


def summarize_atr_stops(trades_df: pd.DataFrame, equity: pd.Series, initial_capital: float, buy_hold_pct: float) -> dict:
    equity = equity.dropna()
    net_pct = (equity.iloc[-1] / initial_capital - 1.0) * 100.0 if len(equity) else 0.0
    running_max = equity.cummax()
    drawdown_pct = (equity / running_max - 1.0) * 100.0
    max_dd_pct = drawdown_pct.min() if len(drawdown_pct) else 0.0

    if trades_df.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "expectancy_r": np.nan,
            "net_return_pct": net_pct,
            "buy_hold_pct": buy_hold_pct,
            "max_drawdown_pct": max_dd_pct,
            "avg_bars_held": np.nan,
        }

    if "open_at_end" in trades_df.columns:
        is_open = trades_df["open_at_end"].fillna(False).astype(bool)
        closed = trades_df[~is_open]
    else:
        closed = trades_df

    wins = closed[closed["r_multiple"] > 0]
    losses = closed[closed["r_multiple"] <= 0]
    gross_profit = wins["r_multiple"].sum()
    gross_loss = -losses["r_multiple"].sum()
    return {
        "trades": len(trades_df),
        "win_rate": len(wins) / len(closed) if len(closed) else np.nan,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": closed["r_multiple"].mean() if len(closed) else np.nan,
        "net_return_pct": net_pct,
        "buy_hold_pct": buy_hold_pct,
        "max_drawdown_pct": max_dd_pct,
        "avg_bars_held": trades_df["bars_held"].mean(),
    }
