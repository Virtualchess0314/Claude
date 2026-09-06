"""
Barrido de parámetros para Hull Suite, con validación train/test para no
sobreajustar (optimizar sobre todo el historial y que después no funcione
en datos nuevos es el error más común al "optimizar" una estrategia).

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --length 20,55,100,180,200 --mode Hma,Ehma,Thma
    python3 optimize.py datos.csv --direction long --min-trades 10

Qué hace:
    1. Carga el CSV (ver data.py para el formato esperado).
    2. Parte el historial en train (primer 70%) / test (30% final) en el
       tiempo — nunca al azar, porque mezclar futuro y pasado en un
       backtest de series de tiempo infla los resultados artificialmente.
    3. Corre cada combinación de (length, mode) en train y en test.
    4. Descarta combinaciones con menos de --min-trades operaciones en test.
    5. Ordena por la métrica pedida en TEST (no en train) — así el ranking
       refleja qué tan bien generaliza cada combinación, no qué tan bien
       memorizó el pasado.
"""

from __future__ import annotations

import argparse
import itertools
import sys

import pandas as pd

from data import load_csv
from engine import Params, simulate


def parse_int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",")]


def parse_str_list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",")]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="CSV de velas (ver data.py)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=10, help="mínimo de operaciones en TEST para no descartar la combinación")
    ap.add_argument("--rank-by", default="test_profit_factor",
                     choices=["test_profit_factor", "test_net_return_pct", "test_avg_trade_pct", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="optimize_results.csv")

    ap.add_argument("--length", type=parse_int_list, default=[20, 55, 100, 180, 200])
    ap.add_argument("--mode", type=parse_str_list, default=["Hma", "Ehma", "Thma"])
    ap.add_argument("--direction", default="all", choices=["long", "short", "all"])
    ap.add_argument("--commission-pct", type=float, default=0.0)
    ap.add_argument("--initial-capital", type=float, default=1000.0)
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(itertools.product(args.length, args.mode))
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    for length, mode in combos:
        p = Params(
            length=length,
            mode=mode,
            direction=args.direction,
            commission_pct=args.commission_pct,
            initial_capital=args.initial_capital,
        )
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)

        rows.append(
            {
                "length": length,
                "mode": mode,
                "train_trades": s_train["trades"],
                "train_profit_factor": s_train["profit_factor"],
                "train_win_rate": s_train["win_rate"],
                "train_net_return_pct": s_train["net_return_pct"],
                "test_trades": s_test["trades"],
                "test_profit_factor": s_test["profit_factor"],
                "test_win_rate": s_test["win_rate"],
                "test_avg_trade_pct": s_test["avg_trade_pct"],
                "test_net_return_pct": s_test["net_return_pct"],
                "test_buy_hold_pct": s_test["buy_hold_pct"],
                "test_max_drawdown_pct": s_test["max_drawdown_pct"],
                "test_avg_bars_held": s_test["avg_bars_held"],
            }
        )

    return pd.DataFrame(rows)


def main():
    args = build_arg_parser().parse_args()
    df = load_csv(args.csv_path)

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    print(
        f"Datos: {len(df)} velas totales · train={len(df_train)} "
        f"({df_train.index[0]} → {df_train.index[-1]}) · "
        f"test={len(df_test)} ({df_test.index[0]} → {df_test.index[-1]})",
        file=sys.stderr,
    )

    results = run_grid(df_train, df_test, args)
    results = results[results["test_trades"] >= args.min_trades].copy()

    if results.empty:
        print(
            "Ninguna combinación llegó al mínimo de operaciones en test "
            f"(--min-trades {args.min_trades}). Bajá el mínimo, usá más "
            "historial o longitudes (`length`) más cortas.",
            file=sys.stderr,
        )
        return

    if args.rank_by == "min_pf_train_test":
        results["rank_metric"] = results[["train_profit_factor", "test_profit_factor"]].min(axis=1)
    else:
        results["rank_metric"] = results[args.rank_by]

    results = results.sort_values("rank_metric", ascending=False).drop(columns="rank_metric")
    results.to_csv(args.out, index=False)
    print(f"\nResultados completos guardados en {args.out}\n", file=sys.stderr)

    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(results.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
