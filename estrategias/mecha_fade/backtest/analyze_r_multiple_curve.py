"""
Curva de winrate/profit-factor por múltiplo de R, para la entrada tal
cual la describió el usuario: mecha por fuera de un indicador (AlphaTrend,
Pivot o DIY -cualquiera de los 3, confluence_need=1) que cierra de nuevo
adentro -> entrar al cierre en el sentido opuesto, con SL más allá del
extremo de la mecha (+ buffer de ATR) y TP a un múltiplo R de ese riesgo.

Corre sobre TODO el dataset (no train/test) porque el objetivo acá es
sólo ver la forma de la curva winrate-vs-R, no validar robustez -para
eso está optimize.py con partición train/test.

Uso:
    python3 analyze_r_multiple_curve.py datos.csv
    python3 analyze_r_multiple_curve.py datos.csv --sl-buffer-atr 0.05,0.10,0.20
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data import load_csv
from engine import Params, simulate

DEFAULT_R_MULTS = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def parse_float_list(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--r-mults", type=parse_float_list, default=DEFAULT_R_MULTS)
    ap.add_argument("--sl-buffer-atr", type=parse_float_list, default=[0.10])
    ap.add_argument("--confluence-need", type=int, default=1)
    ap.add_argument("--out", default="r_multiple_curve.csv")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)

    rows = []
    for sl_buffer_atr in args.sl_buffer_atr:
        for r in args.r_mults:
            p = Params(tp_r_mult=r, sl_buffer_atr=sl_buffer_atr, confluence_need=args.confluence_need)
            _, s = simulate(df, p)
            rows.append({
                "sl_buffer_atr": sl_buffer_atr, "r_mult": r,
                "trades": s["trades"], "win_rate_pct": s["win_rate"] * 100 if s["trades"] else float("nan"),
                "profit_factor": s["profit_factor"], "expectancy_r": s["expectancy_r"],
                "net_pnl_usd": s["net_pnl_usd"],
            })

    rdf = pd.DataFrame(rows)
    rdf.to_csv(args.out, index=False)
    with pd.option_context("display.max_columns", None, "display.width", 160):
        for sl_buffer_atr, group in rdf.groupby("sl_buffer_atr"):
            print(f"\n=== sl_buffer_atr={sl_buffer_atr} ===")
            print(group.drop(columns="sl_buffer_atr").to_string(index=False))


if __name__ == "__main__":
    main()
