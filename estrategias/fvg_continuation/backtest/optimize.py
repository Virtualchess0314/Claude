"""
Barrido de parámetros para FVG Continuación, con validación train/test
para no sobreajustar.

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --min-gap-atr 0.5,0.75,1.0 --min-rr 1.0,1.5,2.0
    python3 optimize.py datos.csv --piv-left 2,3,5 --piv-right 1,2,3

Qué hace:
    1. Carga el CSV (ver data.py para el formato esperado).
    2. Parte el historial en train (primer 70%) / test (30% final) en el
       tiempo — nunca al azar.
    3. Corre cada combinación de parámetros en train y en test por separado.
    4. Descarta combinaciones con menos de --min-trades operaciones en test.
    5. Ordena por la métrica pedida en TEST (no en train).
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


def parse_int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",")]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="CSV de velas (ver data.py)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--rank-by", default="test_profit_factor",
                     choices=["test_profit_factor", "test_expectancy_r", "test_net_pnl_usd", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="optimize_results.csv")

    ap.add_argument("--min-gap-atr", type=parse_float_list, default=[0.5, 0.75, 1.0])
    ap.add_argument("--min-body-ratio", type=parse_float_list, default=[0.5])
    ap.add_argument("--min-range-atr", type=parse_float_list, default=[0.6])
    ap.add_argument("--invalidate-buffer-atr", type=parse_float_list, default=[0.05])
    ap.add_argument("--touch-tol-atr", type=parse_float_list, default=[0.05])
    ap.add_argument("--sl-buffer-atr", type=parse_float_list, default=[0.05, 0.1])
    ap.add_argument("--max-fvg-age", type=parse_int_list, default=[60])
    ap.add_argument("--piv-left", type=parse_int_list, default=[3])
    ap.add_argument("--piv-right", type=parse_int_list, default=[2])
    ap.add_argument("--min-rr", type=parse_float_list, default=[1.0, 1.5, 2.0])

    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--max-risk-usd", type=float, default=300.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.5)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--tick-size", type=float, default=0.25)

    ap.add_argument("--entry-start-hour", type=float, default=None)
    ap.add_argument("--entry-end-hour", type=float, default=None)
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(
        itertools.product(
            args.min_gap_atr, args.min_body_ratio, args.min_range_atr,
            args.invalidate_buffer_atr, args.touch_tol_atr, args.sl_buffer_atr,
            args.max_fvg_age, args.piv_left, args.piv_right, args.min_rr,
        )
    )
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    for (min_gap_atr, min_body_ratio, min_range_atr, invalidate_buffer_atr,
         touch_tol_atr, sl_buffer_atr, max_fvg_age, piv_left, piv_right, min_rr) in combos:
        p = Params(
            min_gap_atr=min_gap_atr, min_body_ratio=min_body_ratio, min_range_atr=min_range_atr,
            invalidate_buffer_atr=invalidate_buffer_atr, touch_tol_atr=touch_tol_atr,
            sl_buffer_atr=sl_buffer_atr, max_fvg_age=max_fvg_age,
            piv_left=piv_left, piv_right=piv_right, min_rr=min_rr,
            atr_len=args.atr_len, max_risk_usd=args.max_risk_usd, point_value_usd=args.point_value_usd,
            max_qty=args.max_qty, commission_round_turn_usd=args.commission_round_turn_usd,
            slippage_ticks=args.slippage_ticks, tick_size=args.tick_size,
            entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour,
        )
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)
        rows.append(
            {
                "min_gap_atr": min_gap_atr, "min_body_ratio": min_body_ratio, "min_range_atr": min_range_atr,
                "invalidate_buffer_atr": invalidate_buffer_atr, "touch_tol_atr": touch_tol_atr,
                "sl_buffer_atr": sl_buffer_atr, "max_fvg_age": max_fvg_age,
                "piv_left": piv_left, "piv_right": piv_right, "min_rr": min_rr,
                "train_trades": s_train["trades"], "train_profit_factor": s_train["profit_factor"],
                "train_win_rate": s_train["win_rate"], "train_expectancy_r": s_train["expectancy_r"],
                "test_trades": s_test["trades"], "test_profit_factor": s_test["profit_factor"],
                "test_win_rate": s_test["win_rate"], "test_expectancy_r": s_test["expectancy_r"],
                "test_net_pnl_usd": s_test["net_pnl_usd"], "test_max_drawdown_usd": s_test["max_drawdown_usd"],
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
            f"(--min-trades {args.min_trades}). Bajá el mínimo o usá más historial.",
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

    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(results.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
