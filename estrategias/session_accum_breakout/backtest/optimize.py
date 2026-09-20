"""
Barrido de parámetros para la estrategia de acumulación pre-sesión +
ruptura + promediadas (ver engine.py para las reglas completas), con
partición train/test 70/30 en el tiempo -misma disciplina que el resto
del repo.

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --session-open-hours 3.0,8.0,12.0,18.0 \
        --lookback-hours 1.0,2.0,3.0,4.0 --max-adds 0,1,2,3 --add-step-atr-mult 0.5,1.0,1.5
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data import load_csv
from engine import Params, simulate


def parse_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def parse_ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--session-open-hours", type=parse_floats, default=[3.0, 8.0, 12.0, 18.0],
                     help="horas de NY (0-24) a probar como apertura de sesión")
    ap.add_argument("--lookback-hours", type=parse_floats, default=[1.0, 2.0, 3.0, 4.0])
    ap.add_argument("--confirm-fraction", type=parse_floats, default=[0.5])
    ap.add_argument("--max-adds", type=parse_ints, default=[0, 1, 2, 3])
    ap.add_argument("--add-step-atr-mult", type=parse_floats, default=[0.5, 1.0, 1.5])
    ap.add_argument("--tp-r-mult", type=float, default=4.0)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--max-risk-usd", type=float, default=750.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--min-trades-train", type=int, default=15)
    ap.add_argument("--min-trades-test", type=int, default=8)
    ap.add_argument("--out", default="optimize_results.csv")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    print(f"Datos: {len(df)} velas · train={len(df_train)} ({df_train.index[0]} -> {df_train.index[-1]}) "
          f"· test={len(df_test)} ({df_test.index[0]} -> {df_test.index[-1]})", file=sys.stderr)

    rows = []
    for open_hour in args.session_open_hours:
        for lookback in args.lookback_hours:
            for confirm_frac in args.confirm_fraction:
                for max_adds in args.max_adds:
                    for step in args.add_step_atr_mult:
                        p = Params(session_open_hour=open_hour, lookback_hours=lookback, confirm_fraction=confirm_frac,
                                   max_adds=max_adds, add_step_atr_mult=step, tp_r_mult=args.tp_r_mult, atr_len=args.atr_len,
                                   sl_buffer_atr=args.sl_buffer_atr, max_risk_usd=args.max_risk_usd,
                                   point_value_usd=args.point_value_usd, max_qty=args.max_qty)
                        _, s_tr = simulate(df_train, p)
                        _, s_te = simulate(df_test, p)
                        rows.append({
                            "session_open_hour": open_hour, "lookback_hours": lookback, "confirm_fraction": confirm_frac,
                            "max_adds": max_adds, "add_step_atr_mult": step,
                            "train_n": s_tr["trades"], "train_pf": s_tr["profit_factor"], "train_exp_r": s_tr["expectancy_r"],
                            "train_wr": s_tr["win_rate"] * 100 if s_tr["trades"] else float("nan"),
                            "test_n": s_te["trades"], "test_pf": s_te["profit_factor"], "test_exp_r": s_te["expectancy_r"],
                            "test_wr": s_te["win_rate"] * 100 if s_te["trades"] else float("nan"),
                        })

    res = pd.DataFrame(rows)
    res.to_csv(args.out, index=False)
    has_sample = res[(res["train_n"] >= args.min_trades_train) & (res["test_n"] >= args.min_trades_test)]
    passed = has_sample[(has_sample["train_pf"] > 1) & (has_sample["test_pf"] > 1)]
    print(f"\nCombos totales: {len(res)}  ·  con muestra suficiente: {len(has_sample)}  ·  pasan train_pf>1 y test_pf>1: {len(passed)}")
    if not passed.empty:
        with pd.option_context("display.max_rows", None, "display.width", 160):
            print(passed.sort_values("test_pf", ascending=False).to_string(index=False))
    print(f"\nResultados completos en {args.out}")


if __name__ == "__main__":
    main()
