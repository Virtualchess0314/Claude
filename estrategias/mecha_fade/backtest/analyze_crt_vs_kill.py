"""
Pedido del usuario: aplicar la metodología ICT CRT (Candle Range Theory)
para entender MEJOR por qué el precio a veces "se regresa" en vez de ir
directo al pivot -en vez de sólo mirar la distancia (ya descartada como
filtro útil, ver runs/2026-09-20_pivot_distance_vs_kill.txt), buscar una
explicación de mecanismo: ¿hubo una manipulación/barrido de liquidez
genuina (CRT) confirmando la dirección del flip, o el flip de AlphaTrend
salió "solo", sin ese respaldo de estructura?

CRT (ver detect_crt() en engine.py): patrón de 3 velas -RANGO,
MANIPULACIÓN (barre liquidez fuera del rango), DISTRIBUCIÓN (cierra de
vuelta adentro, con cierre direccional). Se busca si hubo un CRT a favor
de la nueva tendencia en las `--crt-window` velas antes/en el momento de
cada flip de AlphaTrend con pivot de color contrario (filtro ya
validado).

Para cada temporalidad, compara -igual que se hizo con la distancia al
pivot- el %killed y el MFE entre flips CON CRT de respaldo vs SIN él.

Uso:
    python3 analyze_crt_vs_kill.py datos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from analyze_post_kill_runup import measure_runups
from data import load_csv
from engine import Params, detect_crt


def crt_near_flip(crt: np.ndarray, flip_i: int, direction: float, window: int) -> bool:
    lo = max(0, flip_i - window + 1)
    return bool(np.any(crt[lo:flip_i + 1] == direction))


def annotate_crt(runs: pd.DataFrame, crt: np.ndarray, window: int) -> pd.DataFrame:
    runs = runs.copy()
    direction = runs["direction"].map({"long": 1.0, "short": -1.0})
    runs["has_crt"] = [crt_near_flip(crt, int(fb), d, window) for fb, d in zip(runs["flip_bar"], direction)]
    return runs


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
    ap.add_argument("--crt-window", type=int, default=3,
                     help="cuántas velas antes/en el flip se busca un CRT a favor de la nueva tendencia")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params(at_period=args.at_period, at_mult=args.at_mult, at_use_volume=False,
               piv_period=args.piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=args.piv_atr_factor,
               atr_len=args.atr_len)

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), args.min_segment_bars)
    runs = measure_runups(df, regime, flips, p, args.min_risk_ticks, args.tick_size, require_opposite_color=True)

    crt = detect_crt(df)
    runs = annotate_crt(runs, crt, args.crt_window)

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Flips con R definido: {len(runs)}  ·  con CRT de respaldo: {runs['has_crt'].sum()} ({runs['has_crt'].mean()*100:.1f}%)\n")

    if len(runs) < 20:
        print("Muestra insuficiente.")
        return

    for label, g in [("CON CRT de respaldo", runs[runs["has_crt"]]), ("SIN CRT (flip 'solo')", runs[~runs["has_crt"]])]:
        if g.empty:
            print(f"{label}: sin datos")
            continue
        print(f"{label}: n={len(g)}  %killed={g['killed'].mean()*100:.1f}%  "
              f"MFE mediana(R)={g['mfe_r'].median():.2f}  MFE mediana(ATR)={g['mfe_atr'].median():.2f}  "
              f"%llega a 1R={(g['mfe_r']>=1.0).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
