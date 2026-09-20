"""
Idea del usuario: si hay una entrada confirmada (flip de AlphaTrend con
pivot de color contrario -runs/2026-09-19_opposite_color_pivot_filter.txt),
mirar la temporalidad SIGUIENTE (más alta): si el precio también supera
la nube ahí (buscando matar el pivot de esa temporalidad), darle más
recorrido a la posición; si no, cerrarla más rápido.

Mecánica:
  - Entrada: igual que analyze_alphatrend_flip_entry.py con
    --require-opposite-color-pivot (flip de AlphaTrend en la
    temporalidad BAJA, pivot todavía del color viejo).
  - Mientras la posición está abierta, se mira -de forma CAUSAL, sólo
    velas de la temporalidad ALTA ya cerradas al momento de cada vela
    de la temporalidad baja- si el régimen de AlphaTrend de la
    temporalidad alta pasa a coincidir con la dirección de la posición
    (el precio "supera la nube" ahí).
  - Antes de esa confirmación: TP chico (--tp-r-mult-pre, más
    conservador, cerrar rápido si la temporalidad alta no acompaña).
  - Después de confirmar: se saca el TP y se deja correr con trailing
    por reversión del RÉGIMEN de la temporalidad baja (dar más
    recorrido).
  - SL: ATR puro de la temporalidad baja, igual que
    analyze_alphatrend_flip_entry.py.

Uso:
    python3 analyze_htf_confirmation.py datos_bajos.csv datos_altos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_flip_entry import causal_confirmed_flips
from analyze_alphatrend_kills_pivot import detect_alphatrend_flips
from data import load_csv
from engine import Params, _within_session, pivot_point_supertrend, summarize, wilder_atr


def align_htf_regime(ts_low: pd.DatetimeIndex, df_high: pd.DataFrame, regime_high: np.ndarray, bar_seconds_high: float) -> np.ndarray:
    """
    Para cada vela de la temporalidad baja, devuelve el régimen de
    AlphaTrend de la temporalidad ALTA vigente en ese momento, usando
    sólo velas altas ya CERRADAS (shift hacia adelante por su propia
    duración antes de alinear) -causal, sin look-ahead.
    """
    high_closed_time = df_high.index + pd.Timedelta(seconds=bar_seconds_high)
    s = pd.Series(regime_high, index=high_closed_time).sort_index()
    aligned = s.reindex(s.index.union(ts_low)).ffill().reindex(ts_low)
    return aligned.to_numpy()


def simulate_htf_confirmation(df_low: pd.DataFrame, df_high: pd.DataFrame, p: Params,
                               bar_seconds_high: float, sl_atr_mult: float, tp_r_mult_pre: float) -> tuple[pd.DataFrame, dict]:
    h, l, c = df_low["high"].to_numpy(), df_low["low"].to_numpy(), df_low["close"].to_numpy()
    ts = df_low.index
    n = len(df_low)
    volume_available_low = "volume" in df_low.columns
    volume_available_high = "volume" in df_high.columns

    atr_risk = wilder_atr(df_low["high"], df_low["low"], df_low["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    regime, _ = detect_alphatrend_flips(df_low, p, volume_available_low)
    flips = causal_confirmed_flips(regime, 1)
    flip_set = set(flips.tolist())

    piv_line, piv_trend = pivot_point_supertrend(df_low, p)

    regime_high, _ = detect_alphatrend_flips(df_high, p, volume_available_high)
    regime_high_aligned = align_htf_regime(ts, df_high, regime_high, bar_seconds_high)

    last_day = None
    dtrades = 0
    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0
    entry_risk_pts = np.nan
    htf_confirmed = False

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

            if not htf_confirmed and not np.isnan(regime_high_aligned[i]):
                htf_side = 1.0 if is_long else -1.0
                if regime_high_aligned[i] == htf_side:
                    htf_confirmed = True  # el precio superó la nube en la temporalidad alta

            if htf_confirmed:
                # dar más recorrido: sin TP fijo, trailing por reversión
                # del régimen de la temporalidad BAJA
                if not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            else:
                # todavía sin acompañamiento de la temporalidad alta:
                # TP conservador
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
                    "bars_held": i - entry_bar, "htf_confirmed": htf_confirmed,
                })
                open_pos = False

        if not open_pos and i in flip_set and in_sess and dtrades < p.max_trades_per_day:
            atr_i = atr_risk[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            if np.isnan(piv_trend[i]) or piv_trend[i] == regime[i]:
                continue  # requiere pivot de color contrario (filtro ya validado)
            is_long = regime[i] == 1.0
            entry_signal_price = c[i]
            entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
            entry_bar = i
            if is_long:
                sl = entry_price - sl_atr_mult * atr_i
                risk_pts = entry_price - sl
                tp = entry_price + risk_pts * tp_r_mult_pre
            else:
                sl = entry_price + sl_atr_mult * atr_i
                risk_pts = sl - entry_price
                tp = entry_price - risk_pts * tp_r_mult_pre
            qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
            if qty > 0:
                open_pos = True
                dtrades += 1
                entry_risk_pts = risk_pts
                htf_confirmed = False

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, volume_available_low)
    if not trades_df.empty:
        summary["pct_htf_confirmed"] = trades_df["htf_confirmed"].mean() * 100
    return trades_df, summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_low")
    ap.add_argument("csv_high")
    ap.add_argument("--bar-seconds-high", type=float, required=True, help="duración en segundos de una vela de la temporalidad alta (ej. 300 para 5m)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--sl-atr-mult", type=float, default=2.0)
    ap.add_argument("--tp-r-mult-pre", type=float, default=1.0, help="TP mientras la temporalidad alta no confirma")
    args = ap.parse_args()

    df_low = load_csv(args.csv_low)
    df_high = load_csv(args.csv_high)
    p = Params()

    split_at = int(len(df_low) * args.train_frac)
    df_train, df_test = df_low.iloc[:split_at], df_low.iloc[split_at:]
    high_split_ts = df_train.index[-1]
    df_high_train = df_high[df_high.index <= high_split_ts]
    df_high_test = df_high[df_high.index > high_split_ts]

    print(f"Bajo: {len(df_low)} velas · train={len(df_train)} test={len(df_test)}", file=sys.stderr)
    print(f"Alto: {len(df_high)} velas · train={len(df_high_train)} test={len(df_high_test)}", file=sys.stderr)

    _, s_train = simulate_htf_confirmation(df_train, df_high_train, p, args.bar_seconds_high, args.sl_atr_mult, args.tp_r_mult_pre)
    _, s_test = simulate_htf_confirmation(df_test, df_high_test, p, args.bar_seconds_high, args.sl_atr_mult, args.tp_r_mult_pre)

    for label, s in [("TRAIN", s_train), ("TEST", s_test)]:
        pct = s.get("pct_htf_confirmed", float("nan"))
        print(f"\n{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  "
              f"net_pnl_usd={s['net_pnl_usd']:.0f}  %con_confirmacion_htf={pct:.1f}%")


if __name__ == "__main__":
    main()
