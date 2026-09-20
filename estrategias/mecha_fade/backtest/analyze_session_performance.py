"""
Desglosa el resultado del backtest por sesión de mercado (Asia/Londres/
Londres-NY overlap/NY), según la hora de ENTRADA de cada operación
(hora de Nueva York). Ventanas estándar usadas en análisis de futuros/
forex, no solapadas entre sí (cubren las 24hs):

    Asia            18:00 - 03:00 ET
    Londres         03:00 - 08:00 ET
    Londres/NY      08:00 - 12:00 ET   (overlap, suele ser lo más líquido)
    NY              12:00 - 17:00 ET
    Fuera de sesión 17:00 - 18:00 ET

Uso:
    python3 analyze_session_performance.py datos.csv
    python3 analyze_session_performance.py datos.csv --only-source at --at-mult 2.0 --sl-buffer-atr 1.0 --tp-r-mult 2.0
"""

from __future__ import annotations

import argparse

import pandas as pd

from data import load_csv
from engine import Params, simulate

SESSION_TZ = "America/New_York"


def classify_session(hour: int) -> str:
    if 18 <= hour or hour < 3:
        return "Asia (18-03)"
    if 3 <= hour < 8:
        return "Londres (03-08)"
    if 8 <= hour < 12:
        return "Londres/NY (08-12)"
    if 12 <= hour < 17:
        return "NY (12-17)"
    return "Fuera de sesion (17-18)"


SESSION_ORDER = ["Asia (18-03)", "Londres (03-08)", "Londres/NY (08-12)", "NY (12-17)", "Fuera de sesion (17-18)"]


def summarize_group(g: pd.DataFrame) -> dict:
    wins = g[g["pnl_usd"] > 0]
    losses = g[g["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    return {
        "trades": len(g),
        "win_rate_pct": len(wins) / len(g) * 100 if len(g) else float("nan"),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy_r": g["r_multiple"].mean(),
        "net_pnl_usd": g["pnl_usd"].sum(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--only-source", choices=["at", "piv", "diy"], default=None)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--confluence-need", type=int, default=1)
    ap.add_argument("--tp-r-mult", type=float, default=1.5)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--no-session-close", action="store_true",
                     help="Desactiva el cierre forzado de sesión del motor (no confundir con las ventanas de este análisis)")
    ap.add_argument("--out", default="session_performance.csv")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(only_source=args.only_source, at_mult=args.at_mult, confluence_need=args.confluence_need,
               tp_r_mult=args.tp_r_mult, sl_buffer_atr=args.sl_buffer_atr, use_session=not args.no_session_close)
    trades_df, summary = simulate(df, p)
    print(f"Velas: {len(df)} · operaciones: {summary['trades']} · profit factor global: {summary['profit_factor']:.2f} "
          f"· winrate global: {summary['win_rate'] * 100:.1f}%")
    if trades_df.empty:
        return

    entry_local = trades_df["entry_time"].dt.tz_convert(SESSION_TZ)
    trades_df["session"] = entry_local.dt.hour.map(classify_session)

    rows = []
    for sess in SESSION_ORDER:
        g = trades_df[trades_df["session"] == sess]
        if g.empty:
            continue
        rows.append({"session": sess, **summarize_group(g)})

    result = pd.DataFrame(rows)
    result.to_csv(args.out, index=False)
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(f"\n{result.to_string(index=False)}")
    print(f"\nResultados completos en {args.out}")


if __name__ == "__main__":
    main()
