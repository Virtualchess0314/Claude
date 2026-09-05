"""
Analiza rachas de operaciones GANADORAS consecutivas (a >=1:1 real, no sólo
"cerró en positivo") para evaluar reglas de cuentas de fondeo tipo "X trades
ganadores seguidos para fondear / retirar".

Una operación cuenta como "ganadora >=1:1" sólo si cerró en TP (r_multiple
== rr_target de la config, que en todas las configs vigentes es >=1.0).
Una operación "BE" (breakeven) o "sesión" con r_multiple positivo pero <1.0
NO cuenta, porque no cumple el mínimo 1:1 que exige la regla.

Reporta:
  - p (fracción de operaciones que son "ganadora >=1:1")
  - racha real más larga observada en el historial
  - cuántas veces (puntos de inicio) se dio una racha >= N, para N pedidos
  - tiempo/operaciones esperadas teóricas para ver una racha de N (asumiendo
    independencia, fórmula E_n = (1 - p^n) / ((1-p) * p^n))
  - qty implícito al arriesgar --risk-usd por trade, para chequear que no
    se pase del límite de contratos de la cuenta (lo cual rompería el
    "arriesgar siempre $500" real)

Uso:
    python3 streak_analysis.py datos.csv --min-gap-atr 0.75 --sl-atr-mult 0.75 \
        --rr-target 1.5 --clean-break-buffer-atr 0.10 --risk-usd 500 \
        --entry-start-hour 8 --entry-end-hour 16 --label "5m"
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from data import load_csv
from engine import Params, simulate


def longest_streak_and_occurrences(is_win: list[bool], targets: list[int]) -> dict:
    longest = 0
    current = 0
    occurrences = {n: 0 for n in targets}
    # cuenta "puntos de inicio" de racha >= n: cada vez que la racha ALCANZA
    # exactamente n (no cada vez que la supera), para no contar de más.
    for w in is_win:
        if w:
            current += 1
            longest = max(longest, current)
            for n in targets:
                if current == n:
                    occurrences[n] += 1
        else:
            current = 0
    return {"longest": longest, "occurrences": occurrences}


def expected_trades_for_streak(p: float, n: int) -> float:
    if p <= 0 or p >= 1:
        return float("inf") if p <= 0 else float(n)
    return (1 - p**n) / ((1 - p) * (p**n))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--label", default="")
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--clean-break-buffer-atr", type=float, default=0.10)
    ap.add_argument("--risk-usd", type=float, default=500.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--entry-start-hour", type=float, default=None)
    ap.add_argument("--entry-end-hour", type=float, default=None)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.5)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--resample", default=None, help="p.ej. '10min' para derivar de un csv más chico")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    if args.resample:
        from data import resample_ohlc
        df = resample_ohlc(df, args.resample)

    p = Params(
        min_gap_atr=args.min_gap_atr, sl_atr_mult=args.sl_atr_mult, rr_target=args.rr_target,
        clean_break_buffer_atr=args.clean_break_buffer_atr, max_risk_usd=args.risk_usd,
        point_value_usd=args.point_value_usd, max_qty=args.max_qty,
        entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour,
        commission_round_turn_usd=args.commission_round_turn_usd, slippage_ticks=args.slippage_ticks,
    )
    trades, summary = simulate(df, p)
    n_trades = len(trades)
    if n_trades == 0:
        print(f"[{args.label}] Sin operaciones.")
        return

    # ganadora >=1:1 real = cerró en TP (con rr_target actual, siempre >=1.0
    # en las configs vigentes; si rr_target<1 esto ya no aplicaría tal cual)
    is_qualifying_win = (trades["reason"] == "TP").tolist()
    p_win = float(np.mean(is_qualifying_win))

    res = longest_streak_and_occurrences(is_qualifying_win, targets=[5, 6])

    qty_over_cap = int((trades["qty"] >= args.max_qty).sum())
    avg_qty = trades["qty"].mean()
    max_qty_seen = trades["qty"].max()

    days_span = (df.index[-1] - df.index[0]).total_seconds() / 86400.0
    trades_per_day = n_trades / days_span if days_span > 0 else float("nan")

    print(f"\n{'='*70}\n[{args.label}]  {args.csv_path.split('/')[-1]}  "
          f"({df.index[0].date()} -> {df.index[-1].date()}, {days_span:.0f} días)")
    print(f"  Config: min_gap_atr={p.min_gap_atr} sl_atr_mult={p.sl_atr_mult} "
          f"rr_target={p.rr_target} buffer={p.clean_break_buffer_atr} "
          f"ventana={p.entry_start_hour}-{p.entry_end_hour}")
    print(f"  Total operaciones: {n_trades}  ({trades_per_day:.2f} trades/día)")
    print(f"  Ganadoras >=1:1 real (cerraron en TP): {sum(is_qualifying_win)}  "
          f"-> p = {p_win:.3f} ({p_win*100:.1f}%)")
    print(f"  Racha más larga observada en el historial: {res['longest']}")
    for n in (5, 6):
        occ = res["occurrences"][n]
        print(f"  Veces que se ALCANZÓ una racha de exactamente {n} ganadoras seguidas: {occ}"
              + (f"  (~1 cada {n_trades/occ:.0f} trades)" if occ > 0 else "  (nunca ocurrió en este historial)"))
    for n in (5, 6):
        e_trades = expected_trades_for_streak(p_win, n)
        e_days = e_trades / trades_per_day if trades_per_day > 0 else float("nan")
        print(f"  Teórico (asumiendo independencia, p={p_win:.3f}): trades esperados "
              f"para ver racha de {n} = {e_trades:.0f}  (~{e_days:.0f} días de trading)")
    print(f"  Qty implícito a ${args.risk_usd:.0f} de riesgo: promedio={avg_qty:.1f}, "
          f"máximo={max_qty_seen}, operaciones que tocaron el cap de {args.max_qty}: {qty_over_cap}")


if __name__ == "__main__":
    main()
