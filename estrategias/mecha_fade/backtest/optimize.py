"""
Barrido de parámetros para Mecha Fade, con validación train/test (misma
metodología que ifvg_sniper/upf_artillery): partición 70/30 en el tiempo,
ranking por resultado consistente entre train y test, filtro de mínimo de
operaciones para no confiar en muestras chicas.

Uso:
    python3 optimize.py datos.csv
    python3 optimize.py datos.csv --tp-r-mult 1.0,1.5,2.0 --confluence-need 1,2,3
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
    ap.add_argument("csv_path")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=15)
    ap.add_argument("--rank-by", default="min_pf_train_test",
                     choices=["test_profit_factor", "test_expectancy_r", "test_net_pnl_usd", "min_pf_train_test"])
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="optimize_results.csv")

    ap.add_argument("--at-period", type=parse_int_list, default=[14])
    ap.add_argument("--at-mult", type=parse_float_list, default=[1.0])
    ap.add_argument("--piv-period", type=parse_int_list, default=[2])
    ap.add_argument("--piv-atr-factor", type=parse_float_list, default=[3.0])
    ap.add_argument("--diy-swing-length", type=parse_int_list, default=[10])
    ap.add_argument("--diy-box-width", type=parse_float_list, default=[2.5])
    ap.add_argument("--confluence-need", type=parse_int_list, default=[1])
    ap.add_argument("--sl-buffer-atr", type=parse_float_list, default=[0.10])
    ap.add_argument("--tp-r-mult", type=parse_float_list, default=[1.5])

    ap.add_argument("--piv-atr-period", type=int, default=10)
    ap.add_argument("--diy-atr-len", type=int, default=50)
    ap.add_argument("--diy-overlap-atr-mult", type=float, default=2.0)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--max-risk-usd", type=float, default=150.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--max-trades-per-day", type=int, default=999)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.50)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--no-session-close", action="store_true", help="No forzar cierre de sesión (útil para FX/cripto 24h)")
    ap.add_argument("--entry-start-hour", type=float, default=None, help="Ventana de entrada: hora de inicio (NY, 0-24)")
    ap.add_argument("--entry-end-hour", type=float, default=None, help="Ventana de entrada: hora de fin (NY, 0-24)")
    ap.add_argument("--only-direction", choices=["long", "short"], default=None)
    ap.add_argument("--trailing-exit-source", choices=["at", "piv"], default=None,
                     help="Salida por trailing en vez de TP fijo (ignora --tp-r-mult)")
    ap.add_argument("--fresh-only", action="store_true",
                     help="Filtro de frescura: sólo cuenta la primera mecha que testea cada nivel/zona desde que nació")
    return ap


def run_grid(df_train: pd.DataFrame, df_test: pd.DataFrame, args) -> pd.DataFrame:
    rows = []
    combos = list(itertools.product(
        args.at_period, args.at_mult, args.piv_period, args.piv_atr_factor,
        args.diy_swing_length, args.diy_box_width,
        args.confluence_need, args.sl_buffer_atr, args.tp_r_mult,
    ))
    print(f"Probando {len(combos)} combinaciones...", file=sys.stderr)

    for at_period, at_mult, piv_period, piv_atr_factor, diy_swing_length, diy_box_width, confluence_need, sl_buffer_atr, tp_r_mult in combos:
        p = Params(
            at_period=at_period, at_mult=at_mult,
            piv_period=piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=piv_atr_factor,
            diy_swing_length=diy_swing_length, diy_box_width=diy_box_width,
            diy_atr_len=args.diy_atr_len, diy_overlap_atr_mult=args.diy_overlap_atr_mult,
            confluence_need=confluence_need,
            atr_len=args.atr_len, sl_buffer_atr=sl_buffer_atr, tp_r_mult=tp_r_mult,
            max_risk_usd=args.max_risk_usd, point_value_usd=args.point_value_usd, max_qty=args.max_qty,
            max_trades_per_day=args.max_trades_per_day,
            commission_round_turn_usd=args.commission_round_turn_usd,
            slippage_ticks=args.slippage_ticks, tick_size=args.tick_size,
            use_session=not args.no_session_close,
            entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour,
            fresh_only=args.fresh_only, only_direction=args.only_direction,
            trailing_exit_source=args.trailing_exit_source,
        )
        _, s_train = simulate(df_train, p)
        _, s_test = simulate(df_test, p)
        rows.append({
            "at_period": at_period, "at_mult": at_mult,
            "piv_period": piv_period, "piv_atr_factor": piv_atr_factor,
            "diy_swing_length": diy_swing_length, "diy_box_width": diy_box_width,
            "confluence_need": confluence_need,
            "sl_buffer_atr": sl_buffer_atr, "tp_r_mult": tp_r_mult,
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
        print("AVISO: sin columna de volumen -> AlphaTrend usa RSI en vez de MFI.", file=sys.stderr)

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
