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
