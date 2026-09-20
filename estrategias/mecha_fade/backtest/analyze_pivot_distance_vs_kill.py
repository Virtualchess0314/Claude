"""
Hipótesis del usuario: si el Pivot Point SuperTrend está EXAGERADAMENTE
LEJOS del precio en el momento del flip de AlphaTrend, es más probable
que el precio no llegue a "buscarlo" (matarlo, ver
runs/2026-09-19_pivot_flip_definition_fix.txt) antes de que ocurra OTRO
flip de AlphaTrend -el tramo se termina (reset) antes de que el pivot
viejo confirme la nueva tendencia.

Reutiliza measure_runups() de analyze_post_kill_runup.py, que ya calcula
por cada flip (con pivot de color contrario, filtro ya validado):
  - risk_pts / risk_atr: distancia precio-pivot en el momento del flip
    (normalizada por ATR para poder comparar entre temporalidades).
  - killed: si el pivot llegó a flipear a favor de la nueva tendencia en
    algún momento antes del próximo flip de AlphaTrend.

Se arma risk_atr en cuartiles y se mide %killed y MFE por cuartil, más
la correlación punto-biserial entre risk_atr y killed.

Uso:
    python3 analyze_pivot_distance_vs_kill.py datos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from analyze_post_kill_runup import measure_runups
from data import load_csv
from engine import Params


def point_biserial(binary: pd.Series, continuous: pd.Series) -> float:
    return np.corrcoef(binary.astype(float), continuous)[0, 1]


def spearman(a: pd.Series, b: pd.Series) -> float:
    return np.corrcoef(a.rank(), b.rank())[0, 1]


def bucket_report(runs: pd.DataFrame, n_buckets: int = 4) -> pd.DataFrame:
    runs = runs.copy()
    runs["bucket"] = pd.qcut(runs["risk_atr"], n_buckets, duplicates="drop")
    rows = []
    for b, g in runs.groupby("bucket", observed=True):
        rows.append({
            "bucket": str(b), "n": len(g),
            "risk_atr_median": g["risk_atr"].median(),
            "pct_killed": g["killed"].mean() * 100,
            "mfe_r_median": g["mfe_r"].median(),
            "mfe_atr_median": g["mfe_atr"].median(),
            "pct_reach_1r": (g["mfe_r"] >= 1.0).mean() * 100,
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--piv-period", type=int, default=2)
    ap.add_argument("--piv-atr-period", type=int, default=10)
    ap.add_argument("--piv-atr-factor", type=float, default=3.0)
    ap.add_argument("--at-period", type=int, default=14)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--min-segment-bars", type=int, default=3)
    ap.add_argument("--min-risk-ticks", type=float, default=4.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--n-buckets", type=int, default=4)
    ap.add_argument("--allow-same-color-pivot", action="store_true")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params(at_period=args.at_period, at_mult=args.at_mult, at_use_volume=False,
               piv_period=args.piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=args.piv_atr_factor,
               atr_len=args.atr_len)

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), args.min_segment_bars)
    runs = measure_runups(df, regime, flips, p, args.min_risk_ticks, args.tick_size,
                           require_opposite_color=not args.allow_same_color_pivot)

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Flips con R definido: {len(runs)}\n")

    if len(runs) < 20:
        print("Muestra insuficiente.")
        return

    corr = point_biserial(runs["killed"], runs["risk_atr"])
    corr_mfe_r = spearman(runs["risk_atr"], runs["mfe_r"])
    corr_mfe_atr = spearman(runs["risk_atr"], runs["mfe_atr"])
    print(f"Correlación (distancia pivot en ATR) vs (killed 0/1): r={corr:.3f}")
    print(f"Correlación Spearman (distancia pivot en ATR) vs (MFE en R): rho={corr_mfe_r:.3f}  "
          f"[OJO: R = mfe_pts/risk_pts, y risk_pts ES la distancia -parte de esta correlación es mecánica, no real]")
    print(f"Correlación Spearman (distancia pivot en ATR) vs (MFE en ATR, sin ese sesgo): rho={corr_mfe_atr:.3f}\n")

    rep = bucket_report(runs, args.n_buckets)
    print(rep.to_string(index=False))


if __name__ == "__main__":
    main()
