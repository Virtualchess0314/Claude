"""
Descompone la calidad de cada uno de los 3 indicadores por separado
(AlphaTrend/Pivot/DIY), usando `Params.only_source` para forzar que la
señal dependa de un solo indicador (ignora confluence_need). Objetivo:
saber si alguno tiene ventaja real por sí solo antes de gastar tiempo
optimizando combinaciones -si ninguno individual funciona, la
confluencia entre ellos tampoco va a salvar la estrategia.

También barre parámetros propios de cada indicador (at_mult,
piv_atr_factor/piv_period, diy_swing_length/diy_box_width) cruzados con
tp_r_mult y sl_buffer_atr, para no descartar un indicador solo porque
el default no funciona.

Uso:
    python3 analyze_source_quality.py datos.csv
    python3 analyze_source_quality.py datos1.csv datos2.csv ...  # varios timeframes a la vez
"""

from __future__ import annotations

import argparse
import itertools
import sys

import pandas as pd

from data import load_csv
from engine import Params, simulate

TP_R_MULTS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
SL_BUFFERS = [0.05, 0.10, 0.20, 0.30]

GRIDS = {
    "at": {"at_mult": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]},
    "piv": {"piv_atr_factor": [1.5, 2.0, 2.5, 3.0, 4.0], "piv_period": [2, 3, 5]},
    "diy": {"diy_swing_length": [5, 10, 15, 20], "diy_box_width": [1.0, 1.5, 2.5, 3.5]},
}


def sweep_source(df_train: pd.DataFrame, df_test: pd.DataFrame, src: str, tf: str,
                  min_train: int, min_test: int, fresh_only: bool = False,
                  only_direction: str | None = None) -> list[dict]:
    grid = GRIDS[src]
    keys = list(grid.keys())
    rows = []
    for combo in itertools.product(*grid.values(), TP_R_MULTS, SL_BUFFERS):
        kwargs = dict(zip(keys, combo[: len(keys)]))
        tp_r_mult, sl_buffer_atr = combo[len(keys):]
        p = Params(only_source=src, tp_r_mult=tp_r_mult, sl_buffer_atr=sl_buffer_atr, fresh_only=fresh_only,
                   only_direction=only_direction, **kwargs)
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)
        if s_train["trades"] < min_train or s_test["trades"] < min_test:
            continue
        rows.append({
            "tf": tf, "src": src, **kwargs, "tp_r_mult": tp_r_mult, "sl_buffer_atr": sl_buffer_atr,
            "train_n": s_train["trades"], "train_pf": s_train["profit_factor"], "train_exp": s_train["expectancy_r"],
            "test_n": s_test["trades"], "test_pf": s_test["profit_factor"], "test_exp": s_test["expectancy_r"],
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_paths", nargs="+")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-train-trades", type=int, default=15)
    ap.add_argument("--min-test-trades", type=int, default=10)
    ap.add_argument("--out", default="source_quality_sweep.csv")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--fresh-only", action="store_true",
                     help="Filtro de frescura: sólo cuenta la primera mecha que testea cada nivel/zona desde que nació")
    ap.add_argument("--only-direction", choices=["long", "short"], default=None)
    args = ap.parse_args()

    all_rows = []
    for path in args.csv_paths:
        df = load_csv(path)
        split = int(len(df) * args.train_frac)
        df_train, df_test = df.iloc[:split], df.iloc[split:]
        tf = path.split("/")[-1]
        for src in GRIDS:
            all_rows += sweep_source(df_train, df_test, src, tf, args.min_train_trades, args.min_test_trades,
                                      fresh_only=args.fresh_only, only_direction=args.only_direction)
        print(f"{tf} listo, {len(all_rows)} filas acumuladas", file=sys.stderr)

    rdf = pd.DataFrame(all_rows)
    rdf.to_csv(args.out, index=False)
    rdf["min_pf"] = rdf[["train_pf", "test_pf"]].min(axis=1)
    robust = rdf[(rdf["train_pf"] > 1.0) & (rdf["test_pf"] > 1.0)].sort_values("min_pf", ascending=False)
    print(f"\nResultados completos en {args.out}", file=sys.stderr)
    print(f"Total combos evaluadas: {len(rdf)}")
    print(f"Combos con profit factor > 1 en AMBOS train y test: {len(robust)}\n")
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(robust.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
