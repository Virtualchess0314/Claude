"""
Barrido de parámetros para UPF Artillery, con validación train/test (igual
metodología que el backtester de IFVG Sniper): partición 70/30 en el
tiempo, ranking por resultado en TEST, filtro de mínimo de operaciones
para no confiar en muestras chicas.

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --sd-mult 0.5,1.0,1.5 --sl-mult 1.0,1.5,2.0
"""

from __future__ import annotations

import argparse
import itertools
import sys

import pandas as pd

from data import load_csv
from engine import Params, simulate


def parse_float_list(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=15)
    ap.add_argument("--rank-by", default="min_pf_train_test",
                     choices=["test_profit_factor", "test_expectancy_r", "test_net_pnl_usd", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="optimize_results.csv")

    ap.add_argument("--sd-mult", type=parse_float_list, default=[1.0])
    ap.add_argument("--sl-mult", type=parse_float_list, default=[1.5])
    ap.add_argument("--tp1-mult", type=parse_float_list, default=[1.0])
    ap.add_argument("--tp2-mult", type=parse_float_list, default=[2.0])
    ap.add_argument("--tp3-mult", type=parse_float_list, default=[3.5])
    ap.add_argument("--rsi-bull", type=parse_float_list, default=[40.0])
    ap.add_argument("--rsi-bear", type=parse_float_list, default=[60.0])
    ap.add_argument("--piv-left", type=lambda s: [int(x) for x in s.split(",")], default=[3])
    ap.add_argument("--piv-right", type=lambda s: [int(x) for x in s.split(",")], default=[2])

    ap.add_argument("--base-qty", type=int, default=10)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--commission-per-contract", type=float, default=0.62)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--max-trades", type=int, default=5)
    ap.add_argument("--max-dd-pct", type=float, default=3.5)
    ap.add_argument("--initial-capital", type=float, default=50000.0)
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(itertools.product(
        args.sd_mult, args.sl_mult, args.tp1_mult, args.tp2_mult, args.tp3_mult,
        args.rsi_bull, args.rsi_bear, args.piv_left, args.piv_right,
    ))
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    for sd_mult, sl_mult, tp1_mult, tp2_mult, tp3_mult, rsi_bull, rsi_bear, piv_left, piv_right in combos:
        p = Params(
            sd_mult=sd_mult, sl_mult=sl_mult, tp1_mult=tp1_mult, tp2_mult=tp2_mult, tp3_mult=tp3_mult,
            rsi_bull=rsi_bull, rsi_bear=rsi_bear, piv_left=piv_left, piv_right=piv_right,
            base_qty=args.base_qty, point_value_usd=args.point_value_usd, tick_size=args.tick_size,
            commission_per_contract=args.commission_per_contract, slippage_ticks=args.slippage_ticks,
            max_trades=args.max_trades, max_dd_pct=args.max_dd_pct,
            initial_capital=args.initial_capital,
        )
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)
        rows.append({
            "sd_mult": sd_mult, "sl_mult": sl_mult, "tp1_mult": tp1_mult, "tp2_mult": tp2_mult, "tp3_mult": tp3_mult,
            "rsi_bull": rsi_bull, "rsi_bear": rsi_bear, "piv_left": piv_left, "piv_right": piv_right,
            "train_trades": s_train["trades"], "train_profit_factor": s_train["profit_factor"],
            "train_win_rate": s_train["win_rate"], "train_expectancy_r": s_train["expectancy_r"],
            "test_trades": s_test["trades"], "test_profit_factor": s_test["profit_factor"],
            "test_win_rate": s_test["win_rate"], "test_expectancy_r": s_test["expectancy_r"],
            "test_net_pnl_usd": s_test["net_pnl_usd"], "test_max_drawdown_usd": s_test["max_drawdown_usd"],
            "test_avg_bars_held": s_test["avg_bars_held"],
        })
    return pd.DataFrame(rows)


def main():
    args = build_arg_parser().parse_args()
    df = load_csv(args.csv_path)
    if "volume" not in df.columns:
        print("AVISO: sin columna de volumen en el CSV -> filtro de volumen desactivado.", file=sys.stderr)

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    print(f"Datos: {len(df)} velas · train={len(df_train)} ({df_train.index[0]} -> {df_train.index[-1]}) "
          f"· test={len(df_test)} ({df_test.index[0]} -> {df_test.index[-1]})", file=sys.stderr)

    results = run_grid(df_train, df_test, args)
    results = results[results["test_trades"] >= args.min_trades].copy()
    if results.empty:
        print(f"Ninguna combinación llegó a --min-trades {args.min_trades} en test.", file=sys.stderr)
        return

    if args.rank_by == "min_pf_train_test":
        results["rank_metric"] = results[["train_profit_factor", "test_profit_factor"]].min(axis=1)
    else:
        results["rank_metric"] = results[args.rank_by]
    results = results.sort_values("rank_metric", ascending=False).drop(columns="rank_metric")
    results.to_csv(args.out, index=False)
    print(f"\nResultados completos en {args.out}\n", file=sys.stderr)
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(results.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
