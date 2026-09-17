"""
Post-mortem de las operaciones PERDEDORAS (SL): para cada una, mira qué
hace el precio después del stop y lo clasifica en 3 categorías (a
pedido del usuario):

  1. "barrido_tardio": después de parar el stop, el precio SÍ termina
     yendo hacia donde apuntaba el fade original (llega a como mínimo
     1R en la dirección de la operación) dentro de la ventana de
     lookahead -es decir, el barrido de liquidez tenía razón, pero
     entramos demasiado ajustados / demasiado pronto y el stop nos
     sacó antes de que funcionara.
  2. "breakout_real": después del stop, el precio sigue de largo en
     CONTRA de la operación (hace un nuevo extremo más allá del SL, sin
     volver nunca al precio de entrada) -no era un barrido de liquidez,
     era una ruptura real y el fade estaba mal desde el vamos.
  3. "sin_resolucion": ninguna de las dos cosas pasa claramente dentro
     de la ventana -precio picado/lateral, no valida ni invalida la
     idea original.

También reporta, para los casos "breakout_real", si el indicador que
generó la señal (AlphaTrend y/o Pivot) efectivamente cambió de régimen
(flipeó de lado) cerca del stop -eso corrobora que fue una ruptura real
y no ruido.

Uso:
    python3 analyze_loss_postmortem.py datos.csv
    python3 analyze_loss_postmortem.py datos.csv --lookahead-bars 30 --only-source at --at-mult 2.0
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from data import load_csv
from engine import Params, alpha_trend, pivot_point_supertrend, simulate

CATEGORIES = ("barrido_tardio", "breakout_real", "sin_resolucion")


def classify_trade(row, h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int, lookahead_bars: int) -> dict:
    is_long = row["direction"] == "long"
    entry, sl_level = row["entry"], row["sl"]
    risk = abs(entry - sl_level)
    exit_bar = int(row["exit_bar"])
    window_end = min(exit_bar + lookahead_bars, n)

    target_1r = entry + risk if is_long else entry - risk
    reached_1r = False
    broke_further = False
    worst_extra_r = 0.0

    for j in range(exit_bar, window_end):
        if is_long:
            if h[j] >= target_1r:
                reached_1r = True
                break
            extra = (sl_level - l[j]) / risk if risk > 0 else 0.0
        else:
            if l[j] <= target_1r:
                reached_1r = True
                break
            extra = (h[j] - sl_level) / risk if risk > 0 else 0.0
        worst_extra_r = max(worst_extra_r, extra)
        if extra >= 1.0:
            broke_further = True

    if reached_1r:
        category = "barrido_tardio"
    elif broke_further:
        category = "breakout_real"
    else:
        category = "sin_resolucion"

    return {"category": category, "worst_extra_r": worst_extra_r}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--lookahead-bars", type=int, default=20)
    ap.add_argument("--only-source", choices=["at", "piv", "diy"], default=None)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--confluence-need", type=int, default=1)
    ap.add_argument("--tp-r-mult", type=float, default=1.5)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--out", default="loss_postmortem.csv")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(only_source=args.only_source, at_mult=args.at_mult, confluence_need=args.confluence_need,
               tp_r_mult=args.tp_r_mult, sl_buffer_atr=args.sl_buffer_atr)
    trades_df, summary = simulate(df, p)
    print(f"Velas: {len(df)} · operaciones: {summary['trades']} · profit factor: {summary['profit_factor']:.2f}")

    losers = trades_df[trades_df["pnl_usd"] <= 0].copy()
    print(f"Operaciones perdedoras: {len(losers)} de {len(trades_df)} ({len(losers) / len(trades_df) * 100:.1f}%)")
    if losers.empty:
        return

    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)

    results = losers.apply(lambda row: classify_trade(row, h, l, c, n, args.lookahead_bars), axis=1)
    losers["category"] = [r["category"] for r in results]
    losers["worst_extra_r"] = [r["worst_extra_r"] for r in results]

    # ¿El indicador que generó la señal flipeó de régimen cerca del stop?
    # (sólo tiene sentido para fuentes 'at'/'piv', que son líneas de
    # régimen; 'diy' es una zona, no un régimen que "flipea").
    alpha = alpha_trend(df, p, "volume" in df.columns)
    piv_line, _ = pivot_point_supertrend(df, p)

    def flipped_near_exit(row) -> bool:
        exit_bar = int(row["exit_bar"])
        window_end = min(exit_bar + args.lookahead_bars, n)
        is_long = row["direction"] == "long"
        sources = row["sources"].split(",") if row["sources"] else []
        flipped = False
        if "at" in sources:
            side_at_entry = 1 if is_long else -1
            for j in range(exit_bar, window_end):
                if np.isnan(alpha[j]):
                    continue
                side = 1 if c[j] > alpha[j] else -1
                if side != side_at_entry:
                    flipped = True
                    break
        if "piv" in sources and not flipped:
            side_at_entry = 1 if is_long else -1
            for j in range(exit_bar, window_end):
                if np.isnan(piv_line[j]):
                    continue
                side = 1 if c[j] > piv_line[j] else -1
                if side != side_at_entry:
                    flipped = True
                    break
        return flipped

    losers["indicator_flipped"] = losers.apply(flipped_near_exit, axis=1)
    losers.to_csv(args.out, index=False)

    print(f"\nVentana de lookahead: {args.lookahead_bars} velas después del stop\n")
    counts = losers["category"].value_counts()
    for cat in CATEGORIES:
        n_cat = counts.get(cat, 0)
        pct = n_cat / len(losers) * 100
        print(f"  {cat:16s}: {n_cat:4d} ({pct:5.1f}%)")

    breakouts = losers[losers["category"] == "breakout_real"]
    if len(breakouts) > 0:
        flip_pct = breakouts["indicator_flipped"].mean() * 100
        print(f"\n  De los 'breakout_real': {flip_pct:.1f}% coinciden con un flip de régimen "
              f"del indicador que generó la señal (corrobora que fue ruptura real, no ruido).")

    print(f"\nResultados completos en {args.out}")


if __name__ == "__main__":
    main()
