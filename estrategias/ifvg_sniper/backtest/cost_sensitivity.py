"""
Tabla de sensibilidad a costos (comisión + slippage) para no depender de un
número exacto de comisión que no podemos confirmar desde acá (sin salida a
internet no hay forma de chequear la tarifa vigente de Tradeify/Tradovate).

Corre la config actual con distintos niveles de comisión "round-turn" (ida +
vuelta, por contrato, incluyendo bróker + CME + NFA todo junto) y de
slippage, en train y test por separado, para ver:
  1. Si el edge sobrevive en el escenario realista.
  2. En qué nivel de comisión el profit factor cruza 1.0 (breakeven) — ese
     número importa más que acertarle a una tarifa exacta, porque te dice
     cuánto margen de error tenés.

Uso:
    python3 cost_sensitivity.py datos.csv
"""

from __future__ import annotations

import argparse

import pandas as pd

from data import load_csv
from engine import Params, simulate

# $3.50 round-turn por contrato = confirmado por el usuario desde su cuenta
# de Tradovate/Tradeify (comisión + CME + NFA todo incluido). El resto son
# estimados sólo para ver la sensibilidad alrededor de ese número real.
SCENARIOS = [
    ("Sin costos (ya visto)", 0.0, 0.0),
    ("Sólo CME+NFA (~$0.74 RT, estimado)", 0.74, 0.0),
    ("Bajo (~$1.30 RT, estimado)", 1.30, 0.5),
    ("Medio (~$2.00 RT, estimado)", 2.00, 1.0),
    ("REAL confirmado ($3.50 RT, Tradovate/Tradeify)", 3.50, 1.0),
    ("Muy alto (~$5.00 RT, estimado)", 5.00, 1.5),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--clean-break-buffer-atr", type=float, default=0.10)
    ap.add_argument("--max-risk-usd", type=float, default=300.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--entry-start-hour", type=float, default=8.0)
    ap.add_argument("--entry-end-hour", type=float, default=16.0)
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    split = int(len(df) * args.train_frac)
    train, test = df.iloc[:split], df.iloc[split:]

    base = dict(
        min_gap_atr=args.min_gap_atr,
        sl_atr_mult=args.sl_atr_mult,
        rr_target=args.rr_target,
        clean_break_buffer_atr=args.clean_break_buffer_atr,
        max_risk_usd=args.max_risk_usd,
        point_value_usd=args.point_value_usd,
        tick_size=args.tick_size,
        entry_start_hour=args.entry_start_hour,
        entry_end_hour=args.entry_end_hour,
    )

    rows = []
    for label, commission, slippage in SCENARIOS:
        p = Params(**base, commission_round_turn_usd=commission, slippage_ticks=slippage)
        trades_tr, s_tr = simulate(train, p)
        trades_te, s_te = simulate(test, p)
        avg_qty = trades_te["qty"].mean() if len(trades_te) else float("nan")
        rows.append(
            {
                "escenario": label,
                "comision_rt_usd": commission,
                "slippage_ticks": slippage,
                "avg_qty": avg_qty,
                "train_pf": s_tr["profit_factor"],
                "train_wr": s_tr["win_rate"],
                "test_trades": s_te["trades"],
                "test_pf": s_te["profit_factor"],
                "test_wr": s_te["win_rate"],
                "test_expectancy_r": s_te["expectancy_r"],
                "test_net_pnl_usd": s_te["net_pnl_usd"],
            }
        )

    out = pd.DataFrame(rows)
    with pd.option_context("display.max_columns", None, "display.width", 160, "display.float_format", "{:.3f}".format):
        print(out.to_string(index=False))

    # buscar el nivel de comisión (barrido fino) donde el PF de test cruza 1.0
    print("\nBuscando el punto de equilibrio (PF test = 1.0) con barrido fino de comisión...")
    lo, hi = 0.0, 100.0
    for _ in range(25):
        mid = (lo + hi) / 2
        p = Params(**base, commission_round_turn_usd=mid, slippage_ticks=1.0)
        _, s_te = simulate(test, p)
        pf = s_te["profit_factor"]
        if pf > 1.0:
            lo = mid
        else:
            hi = mid
    print(f"Con 1 tick de slippage en salidas de mercado, el profit factor de TEST "
          f"cruza 1.0 (breakeven) en ~${lo:.2f} de comisión round-turn por contrato.")


if __name__ == "__main__":
    main()
