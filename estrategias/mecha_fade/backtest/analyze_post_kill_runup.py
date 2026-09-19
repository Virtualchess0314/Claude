"""
Sigue a analyze_alphatrend_kills_pivot.py: si al flipear el AlphaTrend el
Pivot Point SuperTrend "muere" (su propio color termina flipeando a favor
de la nueva tendencia -no "el precio vuelve a tocar tal nivel", ver
docstring de test_ppst_pivot_kill en ese script), la pregunta natural es
hasta dónde tiende a llegar el precio después. Esto es lo que se
necesitaría para calibrar una entrada al FLIP de AlphaTrend (SL chico
apoyado en el Pivot recién roto, en vez de esperar la mecha de fade) y un
target/trailing acorde a ese recorrido típico.

Para cada flip de AlphaTrend con pivot de color CONTRARIO (ver
--allow-same-color-pivot):
  - entrada hipotética = close[flip_bar], dirección = nuevo régimen.
  - riesgo (1R) = |entrada - nivel de Pivot en el momento del flip|.
  - se mide el MFE (Maximum Favorable Excursion) del tramo completo hasta
    el próximo flip, tanto en múltiplos de R como en múltiplos de ATR.
  - se separa el resultado en tramos donde el pivot MURIÓ (killed=True:
    su color llegó a igualar el nuevo régimen en algún momento del
    tramo) vs donde nunca lo hizo (killed=False, sigue terco todo el
    tramo), para ver si "matar" el pivot de verdad se asocia a
    recorridos más largos.

Uso:
    python3 analyze_post_kill_runup.py datos.csv
    python3 analyze_post_kill_runup.py datos.csv --min-segment-bars 3
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from data import load_csv
from engine import Params, pivot_point_supertrend, wilder_atr


def measure_runups(df: pd.DataFrame, regime: np.ndarray, flips: np.ndarray, p: Params,
                    min_risk_ticks: float, tick_size: float, require_opposite_color: bool = True) -> pd.DataFrame:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    n = len(df)

    rows = []
    for idx, flip_i in enumerate(flips):
        level = piv_line[flip_i]
        atr_i = atr[flip_i]
        if np.isnan(level) or np.isnan(atr_i) or atr_i <= 0:
            continue
        if require_opposite_color and (np.isnan(piv_trend[flip_i]) or piv_trend[flip_i] == regime[flip_i]):
            continue  # el pivot ya es del mismo color que la nube nueva -no es un nivel viejo para "matar"
        is_long = regime[flip_i] == 1.0
        entry = c[flip_i]
        risk = abs(entry - level)
        if risk < min_risk_ticks * tick_size:
            continue  # pivot pegado al precio: R indefinido, no sirve de referencia

        seg_end = flips[idx + 1] if idx + 1 < len(flips) else n
        target = regime[flip_i]
        mfe = 0.0
        pivot_flipped = False
        bars_to_1r = bars_to_2r = bars_to_3r = np.nan
        for j in range(flip_i + 1, seg_end):
            if not np.isnan(piv_trend[j]) and piv_trend[j] == target:
                pivot_flipped = True
            excursion = (h[j] - entry) if is_long else (entry - l[j])
            if excursion > mfe:
                mfe = excursion
            r_now = mfe / risk
            b = j - flip_i
            if np.isnan(bars_to_1r) and r_now >= 1.0:
                bars_to_1r = b
            if np.isnan(bars_to_2r) and r_now >= 2.0:
                bars_to_2r = b
            if np.isnan(bars_to_3r) and r_now >= 3.0:
                bars_to_3r = b

        rows.append({
            "flip_bar": flip_i, "direction": "long" if is_long else "short",
            "entry": entry, "pivot_level": level, "risk_pts": risk,
            "seg_len_bars": seg_end - flip_i,
            "mfe_pts": mfe, "mfe_r": mfe / risk, "mfe_atr": mfe / atr_i,
            "killed": pivot_flipped,
            "bars_to_1r": bars_to_1r, "bars_to_2r": bars_to_2r, "bars_to_3r": bars_to_3r,
        })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, label: str) -> None:
    if df.empty:
        print(f"{label}: sin datos")
        return
    q = df["mfe_r"].quantile([0.25, 0.5, 0.75, 0.9]).to_dict()
    pct_reach_1r = (df["mfe_r"] >= 1.0).mean() * 100
    pct_reach_2r = (df["mfe_r"] >= 2.0).mean() * 100
    pct_reach_3r = (df["mfe_r"] >= 3.0).mean() * 100
    print(f"{label}: n={len(df)}")
    print(f"  MFE en R  -> p25={q[0.25]:.2f}  mediana={q[0.5]:.2f}  p75={q[0.75]:.2f}  p90={q[0.9]:.2f}  media={df['mfe_r'].mean():.2f}")
    print(f"  MFE en ATR-> mediana={df['mfe_atr'].median():.2f}  p90={df['mfe_atr'].quantile(0.9):.2f}")
    print(f"  Llega a 1R: {pct_reach_1r:.1f}%   a 2R: {pct_reach_2r:.1f}%   a 3R: {pct_reach_3r:.1f}%")
    print(f"  Barras hasta 1R (mediana, sólo los que llegan): {df['bars_to_1r'].median():.1f}")
    print(f"  Duración del tramo (barras, mediana): {df['seg_len_bars'].median():.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--piv-period", type=int, default=2)
    ap.add_argument("--piv-atr-period", type=int, default=10)
    ap.add_argument("--piv-atr-factor", type=float, default=3.0)
    ap.add_argument("--at-period", type=int, default=14)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--atr-len", type=int, default=14, help="ATR usado sólo para normalizar el MFE (no es el de AlphaTrend)")
    ap.add_argument("--min-segment-bars", type=int, default=3,
                     help="ignora flips cuyo tramo dura menos de N velas (filtra whipsaws)")
    ap.add_argument("--min-risk-ticks", type=float, default=4.0,
                     help="ignora flips donde el pivot está a menos de N ticks del precio (R indefinido)")
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--allow-same-color-pivot", action="store_true",
                     help="no filtrar por color del pivot (default: sólo cuenta pivots de color CONTRARIO a la nube nueva)")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params(at_period=args.at_period, at_mult=args.at_mult, at_use_volume=False,
               piv_period=args.piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=args.piv_atr_factor,
               atr_len=args.atr_len)

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), args.min_segment_bars)
    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Flips genuinos analizados: {len(flips)} (de {len(flips_raw)} crudos)", file=sys.stderr)

    runs = measure_runups(df, regime, flips, p, args.min_risk_ticks, args.tick_size,
                           require_opposite_color=not args.allow_same_color_pivot)
    print(f"Flips con R definido (pivot no pegado al precio): {len(runs)}\n")

    summarize(runs, "TODOS los flips")
    print()
    summarize(runs[runs["killed"]], "Pivot MUERTO (killed=True)")
    print()
    summarize(runs[~runs["killed"]], "Pivot NO muerto (se volvió a tocar)")
    print()
    summarize(runs[runs["direction"] == "long"], "Sólo LONG")
    print()
    summarize(runs[runs["direction"] == "short"], "Sólo SHORT")


if __name__ == "__main__":
    main()
