"""
Idea del usuario: cuando el AlphaTrend está por cambiar, las 3 medias
(SMA 10/20/50 -asumido, ver nota abajo) se alinean en el orden de la
nueva tendencia y el precio cierra una vela por ENCIMA (o por debajo)
de las tres, y recién DESPUÉS termina flipeando el AlphaTrend. Probar
entrar en la vela que hace ESE movimiento (el cierre más allá de las 3
medias alineadas) en vez de esperar la vela del flip -sería una entrada
más temprana, con SL apoyado debajo (o encima) de las 3 medias.

⚠️ Supuesto: el usuario escribió "medias de 10-2059", interpretado como
un typo de "10-20-50" (tres períodos de SMA muy estándar). Se dejan
configurables por si hace falta ajustar.

Dos partes:
  1. ¿el patrón realmente ANTICIPA el flip? -mide cuántas barras pasan
     entre la señal de alineación+cierre y el flip real de AlphaTrend
     (causal, sólo cuenta si el flip llega DESPUÉS o en la misma barra).
  2. Backtest real: entrar en la señal de alineación (SL debajo/encima
     de las 3 medias) en vez de en el flip, con el mismo filtro de color
     de pivot ya validado y el mismo modelo de costos/exit que
     analyze_alphatrend_flip_entry.py, para comparar manzana con manzana
     contra el mejor resultado ya conocido.

Uso:
    python3 analyze_ma_alignment_early_entry.py datos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips
from data import load_csv
from engine import Params, _within_session, pivot_point_supertrend, summarize, wilder_atr


def sma(close: pd.Series, length: int) -> np.ndarray:
    return close.rolling(length).mean().to_numpy()


def detect_ma_alignment_signals(df: pd.DataFrame, periods: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Devuelve (signal, ma_fast, ma_mid, ma_slow). `signal[i]` = +1 en la
    PRIMERA vela donde las 3 medias están alineadas en orden alcista
    (rápida > media > lenta) Y el cierre queda por encima de las 3;
    -1 el caso simétrico bajista; 0 el resto. "Primera vela" = flanco de
    subida (no se repite mientras la condición se sostiene, para no
    contar el mismo evento muchas veces).
    """
    c = df["close"].to_numpy()
    fast_p, mid_p, slow_p = periods
    ma_fast = sma(df["close"], fast_p)
    ma_mid = sma(df["close"], mid_p)
    ma_slow = sma(df["close"], slow_p)

    n = len(df)
    signal = np.zeros(n)
    prev_bull = prev_bear = False
    for i in range(n):
        if np.isnan(ma_fast[i]) or np.isnan(ma_mid[i]) or np.isnan(ma_slow[i]):
            continue
        aligned_bull = ma_fast[i] > ma_mid[i] > ma_slow[i]
        aligned_bear = ma_fast[i] < ma_mid[i] < ma_slow[i]
        above_all = c[i] > max(ma_fast[i], ma_mid[i], ma_slow[i])
        below_all = c[i] < min(ma_fast[i], ma_mid[i], ma_slow[i])
        is_bull = aligned_bull and above_all
        is_bear = aligned_bear and below_all
        if is_bull and not prev_bull:
            signal[i] = 1.0
        elif is_bear and not prev_bear:
            signal[i] = -1.0
        prev_bull, prev_bear = is_bull, is_bear
    return signal, ma_fast, ma_mid, ma_slow


def measure_lead_time(df: pd.DataFrame, p: Params, signal: np.ndarray, regime: np.ndarray, max_lookahead: int) -> pd.DataFrame:
    """Para cada señal, ¿el AlphaTrend flipea a favor DESPUÉS (o en la misma barra), y cuántas barras tarda?"""
    n = len(df)
    rows = []
    for i in range(n):
        if signal[i] == 0:
            continue
        target = signal[i]
        flips_ahead = np.nan
        for j in range(i, min(i + max_lookahead + 1, n)):
            if not np.isnan(regime[j]) and regime[j] == target and (j == i or regime[j - 1] != target):
                flips_ahead = j - i
                break
        rows.append({"bar": i, "direction": "long" if target == 1 else "short", "bars_to_flip": flips_ahead})
    return pd.DataFrame(rows)


def simulate_ma_alignment_entries(df: pd.DataFrame, p: Params, signal: np.ndarray, ma_fast: np.ndarray, ma_mid: np.ndarray,
                                   ma_slow: np.ndarray, sl_buffer_atr: float, require_opposite_color_pivot: bool) -> tuple[pd.DataFrame, dict]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    regime, _ = detect_alphatrend_flips(df, p, volume_available)

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
            hit_trail = hit_tp = False
            if p.trailing_exit_source == "regime":
                if not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            else:
                hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)

            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
            elif hit_trail:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "TRAIL"
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
    ap.add_argument("--ma-fast", type=int, default=10)
    ap.add_argument("--ma-mid", type=int, default=20)
    ap.add_argument("--ma-slow", type=int, default=50)
    ap.add_argument("--max-lookahead", type=int, default=20, help="barras máximas a mirar adelante para medir si el AlphaTrend flipea después de la señal")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--tp-r-mult", type=float, default=2.0)
    ap.add_argument("--trailing-exit-source", choices=["regime"], default=None)
    ap.add_argument("--require-opposite-color-pivot", action="store_true")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(tp_r_mult=args.tp_r_mult, trailing_exit_source=args.trailing_exit_source)
    volume_available = "volume" in df.columns

    signal, ma_fast, ma_mid, ma_slow = detect_ma_alignment_signals(df, (args.ma_fast, args.ma_mid, args.ma_slow))
    regime, _ = detect_alphatrend_flips(df, p, volume_available)

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Señales de alineación de medias detectadas: {int((signal != 0).sum())}", file=sys.stderr)

    lead = measure_lead_time(df, p, signal, regime, args.max_lookahead)
    if not lead.empty:
        pct_precedes = lead["bars_to_flip"].notna().mean() * 100
        print(f"\n=== Parte 1: ¿anticipa el flip? ===")
        print(f"% de señales donde el AlphaTrend SÍ flipea a favor dentro de {args.max_lookahead} velas: {pct_precedes:.1f}%")
        valid = lead.dropna(subset=["bars_to_flip"])
        if not valid.empty:
            print(f"Barras hasta el flip (mediana): {valid['bars_to_flip'].median():.1f}  (0 = misma vela)")
            print(f"% que anticipan (bars_to_flip > 0): {(valid['bars_to_flip'] > 0).mean()*100:.1f}%")

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    sig_train, maf_tr, mam_tr, mas_tr = detect_ma_alignment_signals(df_train, (args.ma_fast, args.ma_mid, args.ma_slow))
    sig_test, maf_te, mam_te, mas_te = detect_ma_alignment_signals(df_test, (args.ma_fast, args.ma_mid, args.ma_slow))

    _, s_tr = simulate_ma_alignment_entries(df_train, p, sig_train, maf_tr, mam_tr, mas_tr, args.sl_buffer_atr, args.require_opposite_color_pivot)
    _, s_te = simulate_ma_alignment_entries(df_test, p, sig_test, maf_te, mam_te, mas_te, args.sl_buffer_atr, args.require_opposite_color_pivot)

    print(f"\n=== Parte 2: backtest de la entrada temprana (SL debajo/encima de las 3 medias) ===")
    for label, s in [("TRAIN", s_tr), ("TEST", s_te)]:
        print(f"{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  net_pnl_usd={s['net_pnl_usd']:.0f}")


if __name__ == "__main__":
    main()
