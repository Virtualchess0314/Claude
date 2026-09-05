"""
Analiza, de las operaciones ya cerradas, qué tan cerca estuvo el precio
de la salida CONTRARIA antes de cerrar como cerró:

  - De las GANADORAS (cerraron en TP): ¿cuánto se acercó el precio al SL
    antes de recuperarse y ganar? (Maximum Adverse Excursion, MAE, como
    fracción de la distancia total al SL).
  - De las PERDEDORAS (cerraron en SL): ¿cuánto se acercó el precio al TP
    antes de revertir y perder? (Maximum Favorable Excursion, MFE, como
    fracción de la distancia total al TP).

Uso:
    python3 mae_mfe_analysis.py datos.csv \
        --min-gap-atr 0.75 --sl-atr-mult 0.75 --rr-target 1.5 \
        --clean-break-buffer-atr 0.10 --entry-start-hour 8 --entry-end-hour 16
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from data import load_csv
from engine import Params, simulate


def bucket_report(fracs: pd.Series, label: str) -> None:
    n = len(fracs)
    if n == 0:
        print(f"  Sin operaciones para '{label}'.")
        return
    print(f"  n = {n}")
    edges = [0, 0.25, 0.50, 0.75, 1.00, np.inf]
    names = ["0-25%", "25-50%", "50-75%", "75-100%", ">100% (llegó a tocar o superar)"]
    for lo, hi, name in zip(edges[:-1], edges[1:], names):
        count = ((fracs >= lo) & (fracs < hi)).sum()
        print(f"    {name:35s} {count:4d}  ({count/n*100:5.1f}%)")
    print(f"    -> llegó a >= 25%    del camino: {(fracs >= 0.25).sum():4d} ({(fracs >= 0.25).mean()*100:.1f}%)")
    print(f"    -> llegó a >= 50%    del camino: {(fracs >= 0.50).sum():4d} ({(fracs >= 0.50).mean()*100:.1f}%)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--clean-break-buffer-atr", type=float, default=0.10)
    ap.add_argument("--max-risk-usd", type=float, default=300.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--entry-start-hour", type=float, default=8.0)
    ap.add_argument("--entry-end-hour", type=float, default=16.0)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.5)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(
        min_gap_atr=args.min_gap_atr, sl_atr_mult=args.sl_atr_mult, rr_target=args.rr_target,
        clean_break_buffer_atr=args.clean_break_buffer_atr, max_risk_usd=args.max_risk_usd,
        point_value_usd=args.point_value_usd, max_qty=args.max_qty,
        entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour,
        commission_round_turn_usd=args.commission_round_turn_usd, slippage_ticks=args.slippage_ticks,
    )
    trades, summary = simulate(df, p)
    print(f"Total: {summary['trades']} operaciones sobre {len(df)} velas "
          f"({df.index[0]} -> {df.index[-1]})\n")
    print(trades["reason"].value_counts().to_string(), "\n")

    wins = trades[trades["reason"] == "TP"]
    losses = trades[trades["reason"] == "SL"]

    print("── Ganadoras (cerraron en TP): ¿cuánto se acercaron al SL antes de ganar? " + "─" * 5)
    bucket_report(wins["mae_frac"], "ganadoras")

    print("\n── Perdedoras (cerraron en SL): ¿cuánto se acercaron al TP antes de perder? " + "─" * 5)
    bucket_report(losses["mfe_frac"], "perdedoras")

    other = trades[~trades["reason"].isin(["TP", "SL"])]
    if len(other):
        print(f"\n(Nota: {len(other)} operaciones se cerraron por fin de sesión, no entran en este análisis.)")


if __name__ == "__main__":
    main()
