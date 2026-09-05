"""
Desglosa las operaciones de una config ya elegida por hora del día, día de
la semana, ventana de sesión (Asia/Londres/NY) y dirección (long/short),
para ver DÓNDE está (o no está) el winrate — no para barrer parámetros de
nuevo.

Uso:
    python3 analyze_sessions.py datos.csv \
        --min-gap-atr 0.75 --sl-atr-mult 0.75 --rr-target 1.5 \
        --max-risk-usd 300

IMPORTANTE — por qué esto es descriptivo, no una optimización más:
Cortar el mismo puñado de operaciones en cada vez más categorías (24 horas x
7 días x 3 sesiones x 2 direcciones) es la forma más fácil de "encontrar" un
patrón que en realidad es ruido — cuantos más cortes probás, más probable
que alguno parezca bueno por pura casualidad (data dredging / p-hacking).
Por eso cada tabla imprime también el número de operaciones de cada bucket:
un bucket con 3-5 trades NO es una señal, es ruido con suerte. Sólo tiene
sentido actuar sobre un filtro de horario/sesión si el bucket tiene un
número de operaciones razonable Y el patrón se sostiene igual de fuerte en
train que en test.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from data import load_csv
from engine import Params, simulate

SESSIONS = [
    ("Asia (18:00-03:00 ET)", 18, 27),  # 27 = 03:00 del día siguiente
    ("Londres (03:00-08:00 ET)", 3, 8),
    ("NY apertura (08:00-11:30 ET)", 8, 11.5),
    ("NY tarde (11:30-16:00 ET)", 11.5, 16),
    ("Post-cierre (16:00-18:00 ET)", 16, 18),
]

DOW_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def session_label(hour_et: float) -> str:
    h = hour_et
    for label, start, end in SESSIONS:
        if start <= h < end:
            return label
        if end > 24 and (h < end - 24):  # ventana que cruza medianoche (Asia)
            return label
    return "?"


def summarize_bucket(g: pd.DataFrame) -> dict:
    wins = g[g["pnl_usd"] > 0]
    losses = g[g["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    return {
        "trades": len(g),
        "win_rate": len(wins) / len(g) if len(g) else np.nan,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": g["r_multiple"].mean(),
        "net_pnl_usd": g["pnl_usd"].sum(),
    }


def breakdown(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for val, g in trades.groupby(key):
        rows.append({key: val, **summarize_bucket(g)})
    return pd.DataFrame(rows).sort_values("trades", ascending=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--min-gap-atr", type=float, default=0.75)
    ap.add_argument("--sl-atr-mult", type=float, default=0.75)
    ap.add_argument("--rr-target", type=float, default=1.5)
    ap.add_argument("--min-body-ratio", type=float, default=0.5)
    ap.add_argument("--min-range-atr", type=float, default=0.6)
    ap.add_argument("--max-risk-usd", type=float, default=300.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--max-ifvg-age", type=int, default=60)
    ap.add_argument("--session-tz", default="America/New_York")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(
        min_gap_atr=args.min_gap_atr,
        sl_atr_mult=args.sl_atr_mult,
        rr_target=args.rr_target,
        min_body_ratio=args.min_body_ratio,
        min_range_atr=args.min_range_atr,
        max_risk_usd=args.max_risk_usd,
        point_value_usd=args.point_value_usd,
        atr_len=args.atr_len,
        max_ifvg_age=args.max_ifvg_age,
    )
    trades, summary = simulate(df, p)
    print(f"Total: {summary['trades']} operaciones sobre {len(df)} velas "
          f"({df.index[0]} -> {df.index[-1]})\n")

    if trades.empty:
        print("Sin operaciones, nada para desglosar.")
        return

    et = trades["entry_time"].dt.tz_convert(args.session_tz)
    trades = trades.copy()
    trades["hour_et"] = et.dt.hour + et.dt.minute / 60
    trades["hour_bucket"] = et.dt.hour
    trades["dow"] = et.dt.dayofweek.map(lambda i: DOW_NAMES[i])
    trades["session"] = trades["hour_et"].apply(session_label)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)

    print("── Por hora del día (hora de Nueva York) " + "─" * 40)
    print(breakdown(trades, "hour_bucket").to_string(index=False))

    print("\n── Por ventana de sesión " + "─" * 40)
    print(breakdown(trades, "session").to_string(index=False))

    print("\n── Por día de la semana " + "─" * 40)
    order = {d: i for i, d in enumerate(DOW_NAMES)}
    tbl = breakdown(trades, "dow")
    tbl["_ord"] = tbl["dow"].map(order)
    print(tbl.sort_values("_ord").drop(columns="_ord").to_string(index=False))

    print("\n── Por dirección " + "─" * 40)
    print(breakdown(trades, "direction").to_string(index=False))

    print(
        "\nRecordatorio: un bucket con pocas operaciones (mirá la columna "
        "'trades') es ruido, no una señal para poner un filtro de horario."
    )


if __name__ == "__main__":
    main()
