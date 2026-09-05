"""
Barrido de parámetros para IFVG Sniper, con validación train/test para no
sobreajustar (optimizar sobre todo el historial y que después no funcione
en datos nuevos es el error más común al "optimizar" una estrategia).

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --min-trades 30 --rank-by test_profit_factor
    python3 optimize.py datos.csv --min-gap-atr 0.5,0.75,1.0 --sl-atr-mult 0.5,1.0,1.5

Qué hace:
    1. Carga el CSV (ver data.py para el formato esperado).
    2. Parte el historial en train (primer 70%) / test (30% final) en el
       tiempo — nunca al azar, porque mezclar futuro y pasado en un
       backtest de series de tiempo infla los resultados artificialmente.
    3. Corre cada combinación de parámetros en train y en test por separado.
    4. Descarta combinaciones con menos de --min-trades operaciones (muy
       pocas operaciones = ruido, no señal).
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


def parse_float_list(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="CSV de velas (ver data.py)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20, help="mínimo de operaciones en TEST para no descartar la combinación")
    ap.add_argument("--rank-by", default="test_profit_factor",
                     choices=["test_profit_factor", "test_expectancy_r", "test_net_pnl_usd", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="optimize_results.csv")

    # grid de parámetros (los que más impacto tuvieron según las notas del .pine)
    ap.add_argument("--min-gap-atr", type=parse_float_list, default=[0.75])
    ap.add_argument("--sl-atr-mult", type=parse_float_list, default=[0.75])
    ap.add_argument("--rr-target", type=parse_float_list, default=[1.5])
    ap.add_argument("--min-body-ratio", type=parse_float_list, default=[0.5])
    ap.add_argument("--min-range-atr", type=parse_float_list, default=[0.6])
    ap.add_argument("--max-ifvg-age", type=lambda s: [int(x) for x in s.split(",")], default=[60])
    ap.add_argument("--clean-break-buffer-atr", type=parse_float_list, default=[0.05])
    ap.add_argument("--touch-tol-atr", type=parse_float_list, default=[0.05])

    # fijos (gestión de riesgo / cuenta — normalmente no se barren)
    ap.add_argument("--max-risk-usd", type=float, default=300.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--commission-round-turn-usd", type=float, default=0.0)
    ap.add_argument("--slippage-ticks", type=float, default=0.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--atr-len", type=int, default=14)

    # ventana horaria de entrada — validada en la ronda anterior (sesión NY),
    # queda prendida por defecto para que los barridos siguientes ya partan
    # de ahí. --no-entry-window la apaga para comparar contra 24hs.
    ap.add_argument("--entry-start-hour", type=float, default=8.0)
    ap.add_argument("--entry-end-hour", type=float, default=16.0)
    ap.add_argument("--no-entry-window", action="store_true")
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(
        itertools.product(
            args.min_gap_atr, args.sl_atr_mult, args.rr_target,
            args.min_body_ratio, args.min_range_atr, args.max_ifvg_age,
            args.clean_break_buffer_atr, args.touch_tol_atr,
        )
    )
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    entry_start = None if args.no_entry_window else args.entry_start_hour
    entry_end = None if args.no_entry_window else args.entry_end_hour

    for min_gap_atr, sl_atr_mult, rr_target, min_body_ratio, min_range_atr, max_ifvg_age, clean_break_buffer_atr, touch_tol_atr in combos:
        p = Params(
            min_gap_atr=min_gap_atr,
            sl_atr_mult=sl_atr_mult,
            rr_target=rr_target,
            min_body_ratio=min_body_ratio,
            min_range_atr=min_range_atr,
            max_risk_usd=args.max_risk_usd,
            point_value_usd=args.point_value_usd,
            max_qty=args.max_qty,
            commission_round_turn_usd=args.commission_round_turn_usd,
            slippage_ticks=args.slippage_ticks,
            tick_size=args.tick_size,
            atr_len=args.atr_len,
            max_ifvg_age=max_ifvg_age,
            clean_break_buffer_atr=clean_break_buffer_atr,
            touch_tol_atr=touch_tol_atr,
            entry_start_hour=entry_start,
            entry_end_hour=entry_end,
        )
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)

        rows.append(
            {
                "min_gap_atr": min_gap_atr,
                "sl_atr_mult": sl_atr_mult,
                "rr_target": rr_target,
                "min_body_ratio": min_body_ratio,
                "min_range_atr": min_range_atr,
                "max_ifvg_age": max_ifvg_age,
                "clean_break_buffer_atr": clean_break_buffer_atr,
                "touch_tol_atr": touch_tol_atr,
                "train_trades": s_train["trades"],
                "train_profit_factor": s_train["profit_factor"],
                "train_win_rate": s_train["win_rate"],
                "train_expectancy_r": s_train["expectancy_r"],
                "test_trades": s_test["trades"],
                "test_profit_factor": s_test["profit_factor"],
                "test_win_rate": s_test["win_rate"],
                "test_expectancy_r": s_test["expectancy_r"],
                "test_net_pnl_usd": s_test["net_pnl_usd"],
                "test_max_drawdown_usd": s_test["max_drawdown_usd"],
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
