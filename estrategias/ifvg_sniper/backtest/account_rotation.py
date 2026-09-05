"""
Simula la regla: "si tengo 2 SL seguidos en la cuenta activa, cambio a la
siguiente cuenta" (rotación entre N cuentas de fondeo para no absorber
rachas largas de pérdidas en una sola cuenta).

Como la racha de victorias hacia el objetivo de fondeo ya se resetea a 0
con la 1ra pérdida, cambiar de cuenta recién en la 2da no sacrifica ningún
progreso: sólo limita cada cuenta a un máximo de 2 SL seguidos y traslada
el riesgo de una racha más larga a una cuenta con drawdown en cero.

Reporta:
  - cuántas veces se "quema" una cuenta (evento LL) en el historial
  - separación en trades/días entre quemadas
  - si en algún momento se necesitarían más de N cuentas encadenadas antes
    de que la primera "descanse" (asumiendo que una cuenta quemada no se
    puede reutilizar de inmediato)
"""

from __future__ import annotations

import argparse

import pandas as pd

from data import load_csv, resample_ohlc
from engine import Params, simulate


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--label", default="")
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--clean-break-buffer-atr", type=float, default=0.10)
    ap.add_argument("--risk-usd", type=float, default=500.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--entry-start-hour", type=float, default=None)
    ap.add_argument("--entry-end-hour", type=float, default=None)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.5)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--resample", default=None)
    ap.add_argument("--num-accounts", type=int, default=4)
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    if args.resample:
        df = resample_ohlc(df, args.resample)

    p = Params(
        min_gap_atr=args.min_gap_atr, sl_atr_mult=args.sl_atr_mult, rr_target=args.rr_target,
        clean_break_buffer_atr=args.clean_break_buffer_atr, max_risk_usd=args.risk_usd,
        max_qty=args.max_qty, entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour,
        commission_round_turn_usd=args.commission_round_turn_usd, slippage_ticks=args.slippage_ticks,
    )
    trades, _ = simulate(df, p)
    n_trades = len(trades)

    consec_sl = 0
    account_idx = 0
    switch_events = []  # (trade_index, timestamp, account que se quemó)
    max_consec_per_account = 0
    cur_account_consec = 0

    trades_r = trades.reset_index(drop=True)
    for i, row in trades_r.iterrows():
        if row["reason"] == "SL":
            consec_sl += 1
            cur_account_consec += 1
            max_consec_per_account = max(max_consec_per_account, cur_account_consec)
            if consec_sl >= 2:
                switch_events.append((i, row["exit_time"], account_idx))
                account_idx += 1
                consec_sl = 0
                cur_account_consec = 0
        else:
            consec_sl = 0
            cur_account_consec = 0

    days_span = (df.index[-1] - df.index[0]).total_seconds() / 86400.0
    n_switches = len(switch_events)

    print(f"\n{'='*70}\n[{args.label}] {args.csv_path.split('/')[-1]}  "
          f"({n_trades} trades en {days_span:.0f} días)")
    print(f"  Cuentas 'quemadas' en total, SIN reset diario (evento de 2 SL seguidos): {n_switches}"
          + (f"  (~1 cada {n_trades/n_switches:.1f} trades, ~1 cada {days_span/n_switches:.1f} días)"
             if n_switches else "  (nunca pasó en este historial)"))
    if n_switches:
        gaps_trades = [switch_events[0][0] + 1] + [
            switch_events[j][0] - switch_events[j - 1][0] for j in range(1, n_switches)
        ]
        print(f"  Trades entre quemadas: min={min(gaps_trades)}, "
              f"prom={sum(gaps_trades)/len(gaps_trades):.1f}, max={max(gaps_trades)}")

    # ── Con reset diario: si cada cuenta "quemada" hoy vuelve a estar
    # disponible mañana (típico de cuentas de fondeo con reset de pérdida
    # diaria), lo que importa es el PEOR DÍA: cuántas cuentas hicieron
    # falta ese mismo día calendario.
    trades_r["day"] = pd.to_datetime(trades_r["exit_time"]).dt.date
    worst_day_accounts = 0
    worst_day = None
    days_needing_more_than_n = 0
    per_day_max = {}
    for day, group in trades_r.groupby("day"):
        consec = 0
        acc = 1
        day_max_acc = 1
        for _, row in group.iterrows():
            if row["reason"] == "SL":
                consec += 1
                if consec >= 2:
                    acc += 1
                    day_max_acc = max(day_max_acc, acc)
                    consec = 0
            else:
                consec = 0
        per_day_max[day] = day_max_acc
        if day_max_acc > worst_day_accounts:
            worst_day_accounts = day_max_acc
            worst_day = day
        if day_max_acc > args.num_accounts:
            days_needing_more_than_n += 1

    n_days_with_trades = len(per_day_max)
    print(f"\n  CON reset diario (cuentas quemadas vuelven a estar disponibles al otro día):")
    print(f"    Peor día del historial necesitó {worst_day_accounts} cuenta(s) en simultáneo ({worst_day})")
    print(f"    Días que hubieran necesitado MÁS de {args.num_accounts} cuentas: "
          f"{days_needing_more_than_n} de {n_days_with_trades} días con operaciones "
          f"({days_needing_more_than_n/n_days_with_trades*100:.1f}%)")


if __name__ == "__main__":
    main()
