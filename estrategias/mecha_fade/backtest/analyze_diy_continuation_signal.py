"""
Aclaración del usuario sobre la confluencia triple (ver
runs/2026-09-20_triple_confluence_flip.txt, donde el DIY casi nunca
coincide con la vela del flip): el DIY NO es para la entrada -es una
señal que puede aparecer DESPUÉS, durante el movimiento, y sirve para
decidir si SEGUIR montado en la posición o no.

Secuencia completa:
  1. AlphaTrend flipea (de verde a rojo -> short; de rojo a verde ->
     long). Entrada ya validada.
  2. Se busca que el pivot TAMBIÉN termine flipeando (confirmando la
     nueva tendencia) en algún momento del tramo -el evento "killed" ya
     estudiado en runs/2026-09-19_pivot_flip_definition_fix.txt.
  3. La etiqueta del DIY puede aparecer EN CUALQUIER MOMENTO después del
     flip (antes o después de que el pivot confirme) -tiene que ser del
     MISMO lado (short para el caso bajista, long para el alcista).
  4. Pregunta: si esa etiqueta aparece, ¿es señal de que el movimiento
     tiene más recorrido por delante (conviene seguir montado)?

Se mide, para los tramos con pivot de color contrario al flip (filtro
ya validado): separar por (killed sí/no) x (apareció DIY del mismo lado
en algún momento del tramo sí/no), y comparar el MFE TOTAL del tramo y,
más importante, el recorrido que queda DESPUÉS de que aparece la
etiqueta DIY (¿todavía queda movimiento por capturar en ese punto, o ya
pasó lo grueso?).

Uso:
    python3 analyze_diy_continuation_signal.py datos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from data import load_csv
from engine import Params, pivot_point_supertrend, supply_demand_zones, wilder_atr


def detect_diy_signals(df: pd.DataFrame, p: Params) -> tuple[np.ndarray, np.ndarray]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    atrpoi = wilder_atr(df["high"], df["low"], df["close"], p.diy_atr_len)
    demand_top, demand_bottom, supply_top, supply_bottom, _, _ = supply_demand_zones(h, l, c, atrpoi, p)
    long_diy = ~np.isnan(demand_top) & (l < demand_top) & (c > demand_top)
    short_diy = ~np.isnan(supply_bottom) & (h > supply_bottom) & (c < supply_bottom)
    return long_diy, short_diy


def measure_diy_continuation(df: pd.DataFrame, regime: np.ndarray, flips: np.ndarray, p: Params,
                              min_risk_ticks: float, tick_size: float) -> pd.DataFrame:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    long_diy, short_diy = detect_diy_signals(df, p)
    n = len(df)

    rows = []
    for idx, flip_i in enumerate(flips):
        level = piv_line[flip_i]
        atr_i = atr[flip_i]
        if np.isnan(level) or np.isnan(atr_i) or atr_i <= 0:
            continue
        if np.isnan(piv_trend[flip_i]) or piv_trend[flip_i] == regime[flip_i]:
            continue  # filtro ya validado: pivot de color contrario al momento del flip
        is_long = regime[flip_i] == 1.0
        entry = c[flip_i]
        risk = abs(entry - level)
        if risk < min_risk_ticks * tick_size:
            continue

        seg_end = flips[idx + 1] if idx + 1 < len(flips) else n
        target = regime[flip_i]
        diy_sig = long_diy if is_long else short_diy

        pivot_flipped = False
        diy_bar = None
        mfe_total = 0.0
        mfe_before_diy = 0.0
        for j in range(flip_i + 1, seg_end):
            if not np.isnan(piv_trend[j]) and piv_trend[j] == target:
                pivot_flipped = True
            if diy_bar is None and diy_sig[j]:
                diy_bar = j
            excursion = (h[j] - entry) if is_long else (entry - l[j])
            if excursion > mfe_total:
                mfe_total = excursion
            if diy_bar is None and excursion > mfe_before_diy:
                mfe_before_diy = excursion

        mfe_after_diy = mfe_total - mfe_before_diy if diy_bar is not None else np.nan

        rows.append({
            "flip_bar": flip_i, "direction": "long" if is_long else "short",
            "seg_len_bars": seg_end - flip_i, "killed": pivot_flipped,
            "diy_appeared": diy_bar is not None,
            "diy_bar_offset": (diy_bar - flip_i) if diy_bar is not None else np.nan,
            "mfe_r": mfe_total / risk, "mfe_atr": mfe_total / atr_i,
            "mfe_after_diy_r": mfe_after_diy / risk if diy_bar is not None else np.nan,
            "pct_of_mfe_still_ahead_at_diy": (mfe_after_diy / mfe_total * 100) if (diy_bar is not None and mfe_total > 0) else np.nan,
        })
    return pd.DataFrame(rows)


def summarize_group(g: pd.DataFrame, label: str) -> None:
    if g.empty:
        print(f"  {label}: sin datos")
        return
    print(f"  {label}: n={len(g)}  MFE total mediana(R)={g['mfe_r'].median():.2f}  "
          f"%llega a 1R={(g['mfe_r'] >= 1.0).mean() * 100:.1f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--min-segment-bars", type=int, default=3)
    ap.add_argument("--min-risk-ticks", type=float, default=4.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params()

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), args.min_segment_bars)
    runs = measure_diy_continuation(df, regime, flips, p, args.min_risk_ticks, args.tick_size)

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    if runs.empty:
        print("Muestra insuficiente.")
        return

    print(f"Tramos analizados: {len(runs)}  ·  con etiqueta DIY del mismo lado en algún momento: "
          f"{runs['diy_appeared'].sum()} ({runs['diy_appeared'].mean()*100:.1f}%)\n")

    print("=== MFE total del tramo, por (killed) x (apareció DIY) ===")
    for killed in [True, False]:
        for diy in [True, False]:
            g = runs[(runs["killed"] == killed) & (runs["diy_appeared"] == diy)]
            summarize_group(g, f"killed={killed}  diy_appeared={diy}")

    with_diy = runs[runs["diy_appeared"]]
    if not with_diy.empty:
        print(f"\n=== De los tramos donde apareció DIY: ¿cuánto recorrido quedaba TODAVÍA por delante en ese momento? ===")
        print(f"  n={len(with_diy)}  offset mediana desde el flip: {with_diy['diy_bar_offset'].median():.1f} velas")
        print(f"  MFE ANTES de la etiqueta DIY (mediana R, ya capturado): "
              f"{(with_diy['mfe_r'] - with_diy['mfe_after_diy_r'].fillna(0)).median():.2f}")
        print(f"  MFE DESPUÉS de la etiqueta DIY (mediana R, lo que faltaba): {with_diy['mfe_after_diy_r'].median():.2f}")
        print(f"  % del MFE total que todavía faltaba cuando apareció la etiqueta (mediana): "
              f"{with_diy['pct_of_mfe_still_ahead_at_diy'].median():.1f}%")


if __name__ == "__main__":
    main()
