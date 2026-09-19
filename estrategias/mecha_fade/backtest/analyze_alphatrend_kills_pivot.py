"""
Prueba la hipótesis del usuario: "cada vez que el precio cambia de
AlphaTrend por un breakout previo, mata al pivot" -entendiendo "matar"
como que el nivel de pivot que estaba vigente deja de ser tocado por el
precio durante TODA la vida del nuevo tramo de AlphaTrend (hasta el
próximo flip).

Corre DOS versiones de "pivot":

  1. Pivot Point SuperTrend (el indicador REAL -confirmado con el
     usuario mirando el gráfico: la línea roja/verde escalonada). Se
     toma el valor de esa línea en el momento del flip de AlphaTrend
     como "el nivel en juego", y se revisa si el precio lo vuelve a
     tocar antes del próximo flip.
  2. Pivot SWING genérico (fractal, ta.pivothigh/pivotlow) -referencia
     exploratoria adicional, no es el indicador real del usuario pero
     sirve para comparar contra un swing "puro" sin el suavizado/bandas
     de ATR del Pivot Point SuperTrend. Ante un flip alcista se usa el
     último swing HIGH confirmado (el que la ruptura acaba de romper);
     ante un flip bajista, el último swing LOW.

Para cada flip de AlphaTrend, se identifica el pivot relevante y se
revisa si el precio lo vuelve a tocar (low<=nivel<=high de alguna vela)
en cualquier punto del tramo hasta el próximo flip. Se reporta el % de
flips donde el pivot queda "muerto" (nunca tocado) vs los que no.

Uso:
    python3 analyze_alphatrend_kills_pivot.py datos.csv
    python3 analyze_alphatrend_kills_pivot.py datos.csv --swing-length 3
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from data import load_csv
from engine import (
    Params,
    alpha_trend,
    pivot_point_supertrend,
    pivots,
)


def detect_alphatrend_flips(df: pd.DataFrame, p: Params, volume_available: bool) -> tuple[np.ndarray, np.ndarray]:
    """
    Devuelve (regime, flip_bars). `regime[i]` = 1 (nube verde/alcista) o
    -1 (nube roja/bajista); NaN si todavía no hay suficiente AlphaTrend
    calculado. `flip_bars` = velas donde cambia el color de la nube.

    Replica cómo colorea la nube el indicador AlphaTrend público real:
    comparando el valor de la línea contra el de 2 velas atrás
    (AlphaTrend > AlphaTrend[2] -> verde, AlphaTrend < AlphaTrend[2] ->
    rojo, empate -> mantiene el color anterior).

    CORRECCIÓN (encontrada revisando capturas de gráfico real del
    usuario): una versión anterior de esta función definía el régimen
    como "el cierre está arriba o abajo de la línea", pensando que
    coincidía con lo que se ve en el chart. Es falso: esa definición es
    ~5x más ruidosa que la real. Verificado con datos reales de 5m
    (13-ago, ventana de 5 horas): la definición por pendiente detecta
    1 sólo flip, exactamente donde el usuario lo señala en su chart
    ("Sell" cuando la nube pasa a roja); "cierre vs línea" detectaba 11
    flips falsos en la misma ventana -la línea puede quedar
    momentáneamente entre mechas de precio sin que la tendencia de
    fondo (su propia pendiente) haya cambiado. En todo un archivo de 5m,
    "cierre vs línea" da 1372 flips vs 283 reales por pendiente. Todo
    análisis que dependía de esta función (analyze_post_kill_runup.py,
    analyze_alphatrend_flip_entry.py) se corrió con la definición vieja
    y quedó invalidado -hay que rehacerlo con esta versión.
    """
    at = alpha_trend(df, p, volume_available)
    n = len(df)

    regime = np.full(n, np.nan)
    for i in range(n):
        if i < 2 or np.isnan(at[i]) or np.isnan(at[i - 2]):
            continue
        if at[i] > at[i - 2]:
            regime[i] = 1.0
        elif at[i] < at[i - 2]:
            regime[i] = -1.0
        else:
            regime[i] = regime[i - 1] if not np.isnan(regime[i - 1]) else np.nan

    flips = []
    prev = None
    for i in range(n):
        if np.isnan(regime[i]):
            continue
        if prev is not None and regime[i] != prev:
            flips.append(i)
        prev = regime[i]
    return regime, np.array(flips, dtype=int)


def filter_genuine_flips(regime: np.ndarray, flips: np.ndarray, n: int, min_segment_bars: int) -> np.ndarray:
    """
    Se queda sólo con los flips cuyo tramo resultante dura al menos
    `min_segment_bars` -para separar breakouts genuinos del ruido de
    cruces de 1-3 velas que vuelven enseguida (ver nota en
    detect_alphatrend_flips: ~30% de los flips crudos duran 1 sola vela).
    """
    if min_segment_bars <= 1:
        return flips
    kept = []
    for idx, flip_i in enumerate(flips):
        seg_end = flips[idx + 1] if idx + 1 < len(flips) else n
        if seg_end - flip_i >= min_segment_bars:
            kept.append(flip_i)
    return np.array(kept, dtype=int)


def test_swing_pivot_kill(df: pd.DataFrame, regime: np.ndarray, flips: np.ndarray, swing_length: int) -> dict:
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    piv_h, piv_l = pivots(h, l, swing_length, swing_length)

    last_ph = last_pl = np.nan
    # precomputar último swing confirmado hasta cada bar (para consultarlo en el momento del flip)
    n = len(df)
    last_ph_arr = np.full(n, np.nan)
    last_pl_arr = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(piv_h[i]):
            last_ph = piv_h[i]
        if not np.isnan(piv_l[i]):
            last_pl = piv_l[i]
        last_ph_arr[i] = last_ph
        last_pl_arr[i] = last_pl

    results = []
    for idx, flip_i in enumerate(flips):
        is_bull_flip = regime[flip_i] == 1.0
        level = last_ph_arr[flip_i] if is_bull_flip else last_pl_arr[flip_i]
        if np.isnan(level):
            continue
        seg_end = flips[idx + 1] if idx + 1 < len(flips) else n
        touched = False
        for j in range(flip_i + 1, seg_end):
            if l[j] <= level <= h[j]:
                touched = True
                break
        results.append({"flip_bar": flip_i, "direction": "bull" if is_bull_flip else "bear",
                         "level": level, "seg_len": seg_end - flip_i, "killed": not touched})
    return summarize_kill_results(results)


def test_ppst_pivot_kill(df: pd.DataFrame, regime: np.ndarray, flips: np.ndarray, p: Params,
                          require_opposite_color: bool = True) -> dict:
    """
    "Pivot" = Pivot Point SuperTrend real. Se toma el valor de la línea
    en el momento del flip de AlphaTrend como nivel en juego, y se
    revisa si el precio la vuelve a tocar antes del próximo flip.

    `require_opposite_color` (default True, sugerido por el usuario):
    el Pivot Point SuperTrend tiene su PROPIO color/trend (soporte=1,
    resistencia=-1), independiente del color de la nube de AlphaTrend.
    Sólo tiene sentido hablar de "matar" el pivot cuando, al momento del
    flip, el pivot todavía tiene el color CONTRARIO al nuevo color de la
    nube -es el remanente de la tendencia vieja que la nueva tendencia
    tiene que invalidar. Si el pivot YA es del mismo color que la nube
    nueva, ya está alineado con la tendencia actual -no es el mismo
    fenómeno, y antes se contaba por error. Verificado: es la mayoría de
    los casos (57-63% en 1m/5m, ~49% en 15m).
    """
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    piv_line, piv_trend = pivot_point_supertrend(df, p)
    n = len(df)

    results = []
    for idx, flip_i in enumerate(flips):
        level = piv_line[flip_i]
        if np.isnan(level):
            continue
        if require_opposite_color:
            if np.isnan(piv_trend[flip_i]) or piv_trend[flip_i] == regime[flip_i]:
                continue
        seg_end = flips[idx + 1] if idx + 1 < len(flips) else n
        touched = False
        for j in range(flip_i + 1, seg_end):
            if l[j] <= level <= h[j]:
                touched = True
                break
        results.append({"flip_bar": flip_i, "level": level, "seg_len": seg_end - flip_i, "killed": not touched})
    return summarize_kill_results(results)


def summarize_kill_results(results: list[dict]) -> dict:
    if not results:
        return {"n_flips_tested": 0, "pct_killed": np.nan}
    df = pd.DataFrame(results)
    return {
        "n_flips_tested": len(df),
        "n_killed": int(df["killed"].sum()),
        "pct_killed": df["killed"].mean() * 100,
        "avg_seg_len_bars": df["seg_len"].mean(),
        "detail": df,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--swing-length", type=int, default=3, help="left=right para el pivot swing genérico")
    ap.add_argument("--piv-period", type=int, default=2, help="left=right para el Pivot Point SuperTrend real")
    ap.add_argument("--piv-atr-period", type=int, default=10)
    ap.add_argument("--piv-atr-factor", type=float, default=3.0)
    ap.add_argument("--at-period", type=int, default=14)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--min-segment-bars", type=int, default=1,
                     help="ignora flips cuyo tramo dura menos de N velas (filtra ruido/whipsaws)")
    ap.add_argument("--allow-same-color-pivot", action="store_true",
                     help="no filtrar por color del pivot (default: sólo cuenta pivots de color CONTRARIO a la nube nueva)")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params(at_period=args.at_period, at_mult=args.at_mult, at_use_volume=False,
               piv_period=args.piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=args.piv_atr_factor)

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), args.min_segment_bars)
    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print(f"Flips de AlphaTrend detectados (crudos): {len(flips_raw)}", file=sys.stderr)
    if args.min_segment_bars > 1:
        print(f"Flips tras filtrar tramos < {args.min_segment_bars} velas: {len(flips)}", file=sys.stderr)

    print(f"\n=== Pivot Point SuperTrend (indicador real, period={args.piv_period}) ===")
    r1 = test_ppst_pivot_kill(df, regime, flips, p, require_opposite_color=not args.allow_same_color_pivot)
    print(f"Flips testeados (con Pivot disponible): {r1['n_flips_tested']}")
    if r1["n_flips_tested"] > 0:
        print(f"Pivot 'muerto' (nunca vuelto a tocar) en: {r1['n_killed']} ({r1['pct_killed']:.1f}%)")
        print(f"Duración promedio del tramo: {r1['avg_seg_len_bars']:.1f} velas")

    print(f"\n=== Pivot SWING genérico (fractal, left=right={args.swing_length}) ===")
    r2 = test_swing_pivot_kill(df, regime, flips, args.swing_length)
    print(f"Flips testeados (con pivot swing disponible): {r2['n_flips_tested']}")
    if r2["n_flips_tested"] > 0:
        print(f"Pivot 'muerto' (nunca vuelto a tocar) en: {r2['n_killed']} ({r2['pct_killed']:.1f}%)")
        print(f"Duración promedio del tramo: {r2['avg_seg_len_bars']:.1f} velas")


if __name__ == "__main__":
    main()
