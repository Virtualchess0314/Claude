"""
Refinamiento del usuario sobre la idea de las 3 medias (ver
runs/2026-09-20_ma_alignment_early_entry.txt), con un ejemplo concreto
en 10 minutos: en vez de exigir que las 3 medias (10/20/50) estén
completamente alineadas, la regla es más específica:

  1. AlphaTrend está en régimen NEGATIVO (nube roja/bajista).
  2. El precio viene ACERCÁNDOSE al borde de esa nube (testeando la
     resistencia dinámica de la línea de AlphaTrend desde abajo, sin
     todavía cruzarla) en las velas recientes.
  3. Aparece una vela que CIERRA por encima de la media de 50 Y, en ese
     mismo momento, la media de 10 está por encima de la de 20 (cruce
     parcial, NO hace falta que la de 20 esté también por encima de la
     de 50 -a diferencia de la versión anterior).

Simétrico para el caso bajista (AlphaTrend positivo, precio acercándose
al borde desde arriba, vela que cierra por debajo de la media de 50 con
la de 10 por debajo de la de 20).

No hay CSV nativo de 10 minutos -se remuestrea desde 5m
(`resample_ohlc`, ya validado contra un export real de 15m en otro
análisis del proyecto).

Uso:
    python3 analyze_alpha_edge_ma_cross.py datos_5m.csv --resample 10min
    python3 analyze_alpha_edge_ma_cross.py datos_10m.csv   (si ya es nativo)
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips
from data import load_csv, resample_ohlc
from engine import Params, _within_session, alpha_trend, pivot_point_supertrend, summarize, wilder_atr


def sma(close: pd.Series, length: int) -> np.ndarray:
    return close.rolling(length).mean().to_numpy()


def detect_edge_cross_signals(df: pd.DataFrame, p: Params, volume_available: bool,
                               ma_fast_p: int, ma_mid_p: int, ma_slow_p: int,
                               approach_window: int, approach_atr_mult: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Devuelve (signal, ma_fast, ma_mid, ma_slow, atr). `signal[i]` = +1
    (contexto bajista, precio se acercó al borde de la nube, vela
    rompe con cierre>MA50 y MA10>MA20), -1 el caso simétrico, 0 el
    resto -flanco de subida, no se repite mientras se sostiene.
    """
    c, h, l = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    alpha = alpha_trend(df, p, volume_available)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    regime, _ = detect_alphatrend_flips(df, p, volume_available)

    ma_fast = sma(df["close"], ma_fast_p)
    ma_mid = sma(df["close"], ma_mid_p)
    ma_slow = sma(df["close"], ma_slow_p)

    n = len(df)
    signal = np.zeros(n)
    prev_bull = prev_bear = False
    for i in range(n):
        if i < approach_window or np.isnan(ma_fast[i]) or np.isnan(ma_mid[i]) or np.isnan(ma_slow[i]) \
                or np.isnan(alpha[i]) or np.isnan(atr[i]) or np.isnan(regime[i - 1]):
            continue

        was_bearish = regime[i - 1] == -1.0
        was_bullish = regime[i - 1] == 1.0

        approached_from_below = any(
            not np.isnan(alpha[j]) and (alpha[j] - h[j]) <= approach_atr_mult * atr[j]
            for j in range(i - approach_window, i)
        )
        approached_from_above = any(
            not np.isnan(alpha[j]) and (l[j] - alpha[j]) <= approach_atr_mult * atr[j]
            for j in range(i - approach_window, i)
        )

        is_bull = was_bearish and approached_from_below and c[i] > ma_slow[i] and ma_fast[i] > ma_mid[i]
        is_bear = was_bullish and approached_from_above and c[i] < ma_slow[i] and ma_fast[i] < ma_mid[i]

        if is_bull and not prev_bull:
            signal[i] = 1.0
        elif is_bear and not prev_bear:
            signal[i] = -1.0
        prev_bull, prev_bear = is_bull, is_bear

    return signal, ma_fast, ma_mid, ma_slow, atr


def simulate_edge_cross_entries(df: pd.DataFrame, p: Params, signal: np.ndarray, ma_fast: np.ndarray, ma_mid: np.ndarray,
                                 ma_slow: np.ndarray, sl_buffer_atr: float, require_opposite_color_pivot: bool) -> tuple[pd.DataFrame, dict]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    last_day = None
    dtrades = 0
    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0
    entry_risk_pts = np.nan

    trades = []

    for i in range(n):
        local = ts[i].tz_convert(p.session_tz)
        ct_min = local.hour * 60 + local.minute
        day_id = local.date()
        if day_id != last_day:
            dtrades = 0
            last_day = day_id
        in_sess, is_eod = _within_session(ct_min, p)

        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)
            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
            elif hit_tp:
                exit_price = tp
                exit_reason = "TP"
            elif is_eod:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "EOD"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * qty
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "entry": entry_price, "exit": exit_price, "sl": sl, "reason": exit_reason,
                    "qty": qty, "pnl_usd": pnl,
                    "r_multiple": pnl / (entry_risk_pts * qty * p.point_value_usd) if entry_risk_pts and entry_risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        if not open_pos and signal[i] != 0 and in_sess and dtrades < p.max_trades_per_day:
            atr_i = atr_risk[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            target_regime = signal[i]
            if require_opposite_color_pivot and (np.isnan(piv_trend[i]) or piv_trend[i] == target_regime):
                continue
            is_long = target_regime == 1.0
            entry_signal_price = c[i]
            entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
            entry_bar = i
            if is_long:
                sl = min(ma_fast[i], ma_mid[i], ma_slow[i]) - sl_buffer_atr * atr_i
                risk_pts = entry_price - sl
                tp = entry_price + risk_pts * p.tp_r_mult
            else:
                sl = max(ma_fast[i], ma_mid[i], ma_slow[i]) + sl_buffer_atr * atr_i
                risk_pts = sl - entry_price
                tp = entry_price - risk_pts * p.tp_r_mult
            if risk_pts <= 0:
                continue
            qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd)))
            if qty > 0:
                open_pos = True
                dtrades += 1
                entry_risk_pts = risk_pts

    trades_df = pd.DataFrame(trades)
    return trades_df, summarize(trades_df, volume_available)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--resample", default=None, help="ej. '10min' si el CSV es de una temporalidad más fina")
    ap.add_argument("--ma-fast", type=int, default=10)
    ap.add_argument("--ma-mid", type=int, default=20)
    ap.add_argument("--ma-slow", type=int, default=50)
    ap.add_argument("--approach-window", type=int, default=5, help="velas hacia atrás en las que se busca el acercamiento al borde de la nube")
    ap.add_argument("--approach-atr-mult", type=float, default=0.5, help="qué tan cerca (en ATR) cuenta como 'acercándose al borde'")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--tp-r-mult", type=float, default=2.0)
    ap.add_argument("--require-opposite-color-pivot", action="store_true")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    if args.resample:
        df = resample_ohlc(df, args.resample)
    p = Params(tp_r_mult=args.tp_r_mult)
    volume_available = "volume" in df.columns

    signal, ma_fast, ma_mid, ma_slow, atr = detect_edge_cross_signals(
        df, p, volume_available, args.ma_fast, args.ma_mid, args.ma_slow, args.approach_window, args.approach_atr_mult)

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Señales detectadas: long={int((signal == 1).sum())}  short={int((signal == -1).sum())}", file=sys.stderr)

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    sig_tr, maf_tr, mam_tr, mas_tr, _ = detect_edge_cross_signals(
        df_train, p, volume_available, args.ma_fast, args.ma_mid, args.ma_slow, args.approach_window, args.approach_atr_mult)
    sig_te, maf_te, mam_te, mas_te, _ = detect_edge_cross_signals(
        df_test, p, volume_available, args.ma_fast, args.ma_mid, args.ma_slow, args.approach_window, args.approach_atr_mult)

    _, s_tr = simulate_edge_cross_entries(df_train, p, sig_tr, maf_tr, mam_tr, mas_tr, args.sl_buffer_atr, args.require_opposite_color_pivot)
    _, s_te = simulate_edge_cross_entries(df_test, p, sig_te, maf_te, mam_te, mas_te, args.sl_buffer_atr, args.require_opposite_color_pivot)

    for label, s in [("TRAIN", s_tr), ("TEST", s_te)]:
        print(f"{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  net_pnl_usd={s['net_pnl_usd']:.0f}")


if __name__ == "__main__":
    main()
