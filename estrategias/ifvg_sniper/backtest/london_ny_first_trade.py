"""
Filtra los trades de IFVG Sniper a sólo los primeros N trades de cada día
que hayan entrado dentro de la ventana [9:00 Londres, cierre de sesión NY
(16:45 ET por defecto)].

No usa el filtro de ventana horaria propio del motor (compara contra hora
ET fija) porque Londres y Nueva York no siempre difieren la misma cantidad
de horas (el cambio de horario de verano no ocurre el mismo día en ambos
lados) — así que corre la simulación SIN ventana, y filtra acá con
conversión de zona horaria real a Europe/London por cada vela.

Uso:
    python3 london_ny_first_trade.py datos_5m.csv --risk-usd 300 --max-trades-per-day 2
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from data import load_csv
from engine import Params, simulate


def summarize_subset(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"trades": 0}
    wins = trades_df[trades_df["pnl_usd"] > 0]
    losses = trades_df[trades_df["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    equity = trades_df["pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    return {
        "trades": len(trades_df),
        "win_rate": len(wins) / len(trades_df),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": trades_df["r_multiple"].mean(),
        "net_pnl_usd": trades_df["pnl_usd"].sum(),
        "max_drawdown_usd": drawdown.min(),
        "avg_bars_held": trades_df["bars_held"].mean(),
    }


def print_summary(label: str, s: dict) -> None:
    if s["trades"] == 0:
        print(f"  [{label}] Sin operaciones.")
        return
    print(f"  [{label}] trades={s['trades']}  winrate={s['win_rate']*100:.1f}%  "
          f"PF={s['profit_factor']:.2f}  expectancy={s['expectancy_r']:.2f}R  "
          f"neto=${s['net_pnl_usd']:.0f}  DD_max=${s['max_drawdown_usd']:.0f}  "
          f"duración_prom={s['avg_bars_held']*5:.0f}min")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--clean-break-buffer-atr", type=float, default=0.10)
    ap.add_argument("--risk-usd", type=float, default=300.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.5)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--london-start-hour", type=float, default=9.0)
    ap.add_argument("--session-close-hour", type=int, default=16)
    ap.add_argument("--session-close-minute", type=int, default=45)
    ap.add_argument("--max-trades-per-day", type=int, default=1)
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(
        min_gap_atr=args.min_gap_atr, sl_atr_mult=args.sl_atr_mult, rr_target=args.rr_target,
        clean_break_buffer_atr=args.clean_break_buffer_atr, max_risk_usd=args.risk_usd,
        max_qty=args.max_qty, commission_round_turn_usd=args.commission_round_turn_usd,
        slippage_ticks=args.slippage_ticks, session_close_hour=args.session_close_hour,
        session_close_minute=args.session_close_minute,
        entry_start_hour=None, entry_end_hour=None,  # sin ventana propia: filtramos abajo
    )
    trades, _ = simulate(df, p)
    days_span = (df.index[-1] - df.index[0]).total_seconds() / 86400.0

    print(f"\n{'='*72}\nMNQ 5m — {args.csv_path.split('/')[-1]}  "
          f"({df.index[0].date()} -> {df.index[-1].date()}, {days_span:.0f} días)")
    print(f"Config: min_gap_atr={p.min_gap_atr} sl_atr_mult={p.sl_atr_mult} "
          f"rr_target={p.rr_target} buffer={p.clean_break_buffer_atr} riesgo=${p.max_risk_usd:.0f}\n")

    print_summary("Todos los trades (sin filtro de ventana/orden)", summarize_subset(trades))

    trades = trades.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["entry_london_hour"] = trades["entry_time"].dt.tz_convert("Europe/London")
    trades["entry_london_hourfloat"] = (
        trades["entry_london_hour"].dt.hour + trades["entry_london_hour"].dt.minute / 60
    )
    trades["entry_ny_date"] = trades["entry_time"].dt.tz_convert("America/New_York").dt.date

    in_window = trades[trades["entry_london_hourfloat"] >= args.london_start_hour]
    print_summary(
        f"Sólo entradas desde las {args.london_start_hour:.0f}:00 Londres "
        f"hasta el cierre NY ({args.session_close_hour}:{args.session_close_minute:02d} ET)",
        summarize_subset(in_window),
    )

    n = args.max_trades_per_day
    capped = (
        in_window.sort_values("entry_time")
        .groupby("entry_ny_date", as_index=False, group_keys=False)
        .head(n)
    )
    label = "Sólo el PRIMER trade del día dentro de esa ventana" if n == 1 else \
        f"Máximo {n} trades por día dentro de esa ventana (se toman los primeros {n} en orden cronológico)"
    print_summary(label, summarize_subset(capped))

    n_days_with_signal = in_window["entry_ny_date"].nunique()
    n_days_total = df.index.tz_convert("America/New_York").normalize().nunique()
    print(f"\n  Días con al menos 1 señal en la ventana: {n_days_with_signal} de "
          f"~{n_days_total} días de historial ({n_days_with_signal/n_days_total*100:.1f}%)")
    multi_signal_days = in_window.groupby("entry_ny_date").size()
    print(f"  Días con MÁS de 1 señal en la ventana (o sea, se habría descartado "
          f"la 2da+ para quedarse sólo con la 1ra): {(multi_signal_days > 1).sum()}")


if __name__ == "__main__":
    main()
