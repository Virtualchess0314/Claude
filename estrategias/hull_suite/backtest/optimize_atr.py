"""
Barrido de SL/TP por ATR para la variante `simulate_atr_stops` de Hull
Suite (ver docstring de engine.py) — para responder concretamente "¿qué
múltiplo de ATR le conviene al SL, y qué R:R (1:1 / 1:1.5 / 1:2) rinde
mejor?", con validación train/test para no sobreajustar.

Uso:
    python3 optimize_atr.py datos_5m.csv
    python3 optimize_atr.py datos_1m.csv --sl-atr-mult 0.5,0.75,1.0,1.5,2.0 --rr-target 1.0,1.5,2.0
    python3 optimize_atr.py datos_15m.csv --length 34,55,89 --mode Hma,Ehma

Corré esto UNA VEZ POR CADA TIMEFRAME (1m/3m/5m/10m/15m tienen su propio
CSV — no se puede derivar uno del otro salvo que uno sea múltiplo exacto
del otro, ver nota en el README).

Qué hace:
    1. Carga el CSV (ver data.py para el formato esperado).
    2. Parte el historial en train (primer 70%) / test (30% final) en el
       tiempo — nunca al azar, para no inflar resultados con look-ahead.
    3. Corre cada combinación de (length, mode, sl_atr_mult, rr_target) en
       train y en test por separado.
    4. Descarta combinaciones con menos de --min-trades operaciones en test.
    5. Ordena por la métrica pedida en TEST (no en train).
"""

from __future__ import annotations

import argparse
import itertools
import sys

import pandas as pd

from data import load_csv
from engine import StopParams, simulate_atr_stops


def parse_float_list(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def parse_int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",")]


def parse_str_list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",")]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="CSV de velas de UN SOLO timeframe (ver data.py)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20, help="mínimo de operaciones en TEST para no descartar la combinación")
    ap.add_argument("--rank-by", default="test_profit_factor",
                     choices=["test_profit_factor", "test_expectancy_r", "test_net_return_pct", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default="optimize_atr_results.csv")

    ap.add_argument("--length", type=parse_int_list, default=[55])
    ap.add_argument("--mode", type=parse_str_list, default=["Hma"])
    ap.add_argument("--direction", default="all", choices=["long", "short", "all"])
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--sl-atr-mult", type=parse_float_list, default=[0.5, 0.75, 1.0, 1.5, 2.0, 3.0])
    ap.add_argument("--rr-target", type=parse_float_list, default=[1.0, 1.5, 2.0])
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--commission-pct", type=float, default=0.0)
    ap.add_argument("--initial-capital", type=float, default=1000.0)
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(itertools.product(args.length, args.mode, args.sl_atr_mult, args.rr_target))
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    for length, mode, sl_atr_mult, rr_target in combos:
        sp = StopParams(
            length=length,
            mode=mode,
            direction=args.direction,
            atr_len=args.atr_len,
            sl_atr_mult=sl_atr_mult,
            rr_target=rr_target,
            risk_pct=args.risk_pct,
            commission_pct=args.commission_pct,
            initial_capital=args.initial_capital,
        )
        _, s_train = simulate_atr_stops(df_train, sp)
        _, s_test = simulate_atr_stops(df_test, sp)

        rows.append(
            {
                "length": length,
                "mode": mode,
                "sl_atr_mult": sl_atr_mult,
                "rr_target": rr_target,
                "train_trades": s_train["trades"],
                "train_profit_factor": s_train["profit_factor"],
                "train_win_rate": s_train["win_rate"],
                "train_expectancy_r": s_train["expectancy_r"],
                "test_trades": s_test["trades"],
                "test_profit_factor": s_test["profit_factor"],
                "test_win_rate": s_test["win_rate"],
                "test_expectancy_r": s_test["expectancy_r"],
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

    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(results.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
