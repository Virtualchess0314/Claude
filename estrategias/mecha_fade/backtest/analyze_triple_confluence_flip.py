"""
Idea del usuario: entrar en la vela del flip de AlphaTrend (la entrada
ya validada, ver runs/2026-09-19_opposite_color_pivot_filter.txt) pero
exigiendo una confluencia TRIPLE en vez de sólo el color del pivot:

  1. AlphaTrend flipea (entrada, como siempre).
  2. En esa MISMA vela hay una etiqueta de señal del DIY (long/short,
     mecha contra una zona de oferta/demanda -ver supply_demand_zones()
     en engine.py, la 3ra pata original de mecha_fade).
  3. El Pivot Point SuperTrend TAMBIÉN flipea -no sólo que sea de color
     contrario al momento del flip (el filtro ya validado), sino que
     efectivamente CONFIRME (cambie de color) cerca de esa misma vela.

Hipótesis: cuando los 3 indicadores originales de mecha_fade (AlphaTrend,
Pivot, DIY) se alinean/confirman juntos, es más probable que el
movimiento sea grande.

Dos partes:
  1. Correlacional: de los flips con pivot de color contrario (filtro ya
     validado), comparar %killed y MFE entre los que TIENEN cada
     confluencia extra (DIY, pivot-flip-cercano) vs los que no.
  2. Backtest real: usar ambas condiciones como filtro de entrada
     adicional sobre analyze_alphatrend_flip_entry.simulate_flip_entries,
     comparando contra el baseline ya conocido.

Uso:
    python3 analyze_triple_confluence_flip.py datos.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_flip_entry import causal_confirmed_flips, simulate_flip_entries
from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from analyze_post_kill_runup import measure_runups
from data import load_csv
from engine import Params, pivot_point_supertrend, supply_demand_zones, wilder_atr


def detect_diy_signals(df: pd.DataFrame, p: Params) -> tuple[np.ndarray, np.ndarray]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    atrpoi = wilder_atr(df["high"], df["low"], df["close"], p.diy_atr_len)
    demand_top, demand_bottom, supply_top, supply_bottom, _, _ = supply_demand_zones(h, l, c, atrpoi, p)
    long_diy = ~np.isnan(demand_top) & (l < demand_top) & (c > demand_top)
    short_diy = ~np.isnan(supply_bottom) & (h > supply_bottom) & (c < supply_bottom)
    return long_diy, short_diy


def pivot_flips_near(piv_trend: np.ndarray, i: int, target: float, window: int) -> bool:
    """
    ⚠️ NO CAUSAL -mira velas FUTURAS (i+window). Sólo sirve para la
    Parte 1 (estadística retrospectiva/explicativa: "de los flips que
    YA pasaron, ¿el pivot terminó confirmando cerca?"). NO se puede usar
    como filtro de entrada en tiempo real (en el momento de la vela del
    flip no se sabe todavía si el pivot va a flipear en las próximas N
    velas) -por eso la Parte 2 (backtest con plata real) usa
    `diy_signal_causal_window` en su lugar, que sí es 100% causal.
    """
    n = len(piv_trend)
    lo, hi = max(1, i - window), min(n, i + window + 1)
    for j in range(lo, hi):
        if not np.isnan(piv_trend[j]) and not np.isnan(piv_trend[j - 1]) and piv_trend[j] != piv_trend[j - 1] and piv_trend[j] == target:
            return True
    return False


def diy_signal_causal_window(long_diy: np.ndarray, short_diy: np.ndarray, i: int, target: float, window: int) -> bool:
    """Versión CAUSAL (sólo mira velas <= i): ¿hubo una señal DIY a favor en las últimas `window` velas hasta el flip inclusive?"""
    lo = max(0, i - window)
    sig = long_diy if target == 1.0 else short_diy
    return bool(np.any(sig[lo:i + 1]))


def part1_correlational(df: pd.DataFrame, p: Params, volume_available: bool, pivot_flip_window: int,
                         diy_window: int, min_segment_bars: int, min_risk_ticks: float, tick_size: float) -> None:
    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, len(df), min_segment_bars)
    runs = measure_runups(df, regime, flips, p, min_risk_ticks, tick_size, require_opposite_color=True)
    if runs.empty:
        print("Muestra insuficiente.")
        return

    long_diy, short_diy = detect_diy_signals(df, p)
    _, piv_trend = pivot_point_supertrend(df, p)

    direction = runs["direction"].map({"long": 1.0, "short": -1.0})
    has_diy_same_bar = []
    has_diy_window = []
    has_pivot_flip = []
    for fb, d in zip(runs["flip_bar"], direction):
        fb = int(fb)
        has_diy_same_bar.append(bool(long_diy[fb] if d == 1.0 else short_diy[fb]))
        has_diy_window.append(diy_signal_causal_window(long_diy, short_diy, fb, d, diy_window))
        has_pivot_flip.append(pivot_flips_near(piv_trend, fb, d, pivot_flip_window))
    runs["has_diy_same_bar"] = has_diy_same_bar
    runs["has_diy_window"] = has_diy_window
    runs["has_pivot_flip_near"] = has_pivot_flip
    runs["n_extra_confluences"] = runs["has_diy_window"].astype(int) + runs["has_pivot_flip_near"].astype(int)

    print(f"Flips analizados: {len(runs)}  ·  con DIY en la MISMA vela: {runs['has_diy_same_bar'].sum()} "
          f"({runs['has_diy_same_bar'].mean()*100:.1f}%)  ·  con DIY en las últimas {diy_window} velas (causal): "
          f"{runs['has_diy_window'].sum()} ({runs['has_diy_window'].mean()*100:.1f}%)  ·  con pivot-flip cercano "
          f"(⚠️ NO causal, sólo referencia): {runs['has_pivot_flip_near'].sum()} ({runs['has_pivot_flip_near'].mean()*100:.1f}%)")

    for n_extra in [0, 1, 2]:
        g = runs[runs["n_extra_confluences"] == n_extra]
        if g.empty:
            continue
        print(f"  {n_extra} confluencia(s) extra: n={len(g):3d}  %killed={g['killed'].mean()*100:5.1f}%  "
              f"MFE mediana(R)={g['mfe_r'].median():5.2f}  MFE mediana(ATR)={g['mfe_atr'].median():5.2f}  "
              f"%llega a 1R={(g['mfe_r']>=1.0).mean()*100:5.1f}%")


def part2_backtest(csv_path: str, train_frac: float, sl_atr_mult: float, tp_r_mult: float,
                    require_diy_same_bar: bool, diy_window: int | None) -> None:
    """
    ⚠️ Sólo usa filtros 100% CAUSALES: DIY en la misma vela del flip, o
    DIY en las últimas `diy_window` velas (ambos verificables en el
    momento de entrar). El "pivot también flipea" de la Parte 1 NO se
    puede usar acá -requeriría saber el futuro en el momento de entrar
    (ver nota en pivot_flips_near). La versión causal de "el pivot
    confirma" ya se probó por separado como entrada/salida y no ayudó
    -ver runs/2026-09-19_pivot_confirmation_entry.txt y
    runs/2026-09-19_pivot_confirms_as_exit.txt.
    """
    df = load_csv(csv_path)
    p = Params(tp_r_mult=tp_r_mult)

    split_at = int(len(df) * train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]

    for label, d in [("TRAIN", df_train), ("TEST", df_test)]:
        _, s = simulate_triple_confluence_entries(d, p, sl_atr_mult, require_diy_same_bar, diy_window)
        print(f"{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  net_pnl_usd={s['net_pnl_usd']:.0f}")


def simulate_triple_confluence_entries(df: pd.DataFrame, p: Params, sl_atr_mult: float,
                                        require_diy_same_bar: bool, diy_window: int | None) -> tuple[pd.DataFrame, dict]:
    """Reusa simulate_flip_entries pero pre-filtrando qué flips son elegibles según DIY (100% causal)."""
    volume_available = "volume" in df.columns
    regime, _ = detect_alphatrend_flips(df, p, volume_available)
    flips = causal_confirmed_flips(regime, 1)
    _, piv_trend = pivot_point_supertrend(df, p)
    long_diy, short_diy = detect_diy_signals(df, p)

    eligible = np.zeros(len(df), dtype=bool)
    for fb in flips:
        target = regime[fb]
        if np.isnan(target):
            continue
        if np.isnan(piv_trend[fb]) or piv_trend[fb] == target:
            continue  # filtro ya validado: pivot debe ser de color contrario al momento del flip
        if require_diy_same_bar and not bool(long_diy[fb] if target == 1.0 else short_diy[fb]):
            continue
        if diy_window is not None and not diy_signal_causal_window(long_diy, short_diy, fb, target, diy_window):
            continue
        eligible[fb] = True

    return simulate_flip_entries_masked(df, p, sl_atr_mult, eligible)


def simulate_flip_entries_masked(df: pd.DataFrame, p: Params, sl_atr_mult: float, eligible: np.ndarray) -> tuple[pd.DataFrame, dict]:
    from engine import _within_session, pivot_point_supertrend, summarize, wilder_atr as watr
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns
    atr_risk = watr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size
    regime, _ = detect_alphatrend_flips(df, p, volume_available)

    last_day = None
    dtrades = 0
    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0
    entry_risk_pts = np.nan
    trades = []

    for i in range(n):
        local = ts[i].tz_convert(p.session_tz)
        ct_min = local.hour * 60 + local.minute
        day_id = local.date()
        if day_id != last_day:
            dtrades = 0
            last_day = day_id
        in_sess, is_eod = _within_session(ct_min, p)

        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            hit_trail = hit_tp = False
            if p.trailing_exit_source == "regime":
                if not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            else:
                hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)

            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
            elif hit_trail:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "TRAIL"
            elif hit_tp:
                exit_price = tp
                exit_reason = "TP"
            elif is_eod:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "EOD"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * qty
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "entry": entry_price, "exit": exit_price, "sl": sl, "reason": exit_reason,
                    "qty": qty, "pnl_usd": pnl,
                    "r_multiple": pnl / (entry_risk_pts * qty * p.point_value_usd) if entry_risk_pts and entry_risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        if not open_pos and eligible[i] and in_sess and dtrades < p.max_trades_per_day:
            atr_i = atr_risk[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            is_long = regime[i] == 1.0
            entry_signal_price = c[i]
            entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
            entry_bar = i
            if is_long:
                sl = entry_price - sl_atr_mult * atr_i
                risk_pts = entry_price - sl
                tp = entry_price + risk_pts * p.tp_r_mult
            else:
                sl = entry_price + sl_atr_mult * atr_i
                risk_pts = sl - entry_price
                tp = entry_price - risk_pts * p.tp_r_mult
            qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
            if qty > 0:
                open_pos = True
                dtrades += 1
                entry_risk_pts = risk_pts

    trades_df = pd.DataFrame(trades)
    return trades_df, summarize(trades_df, volume_available)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--pivot-flip-window", type=int, default=5,
                     help="⚠️ NO causal, sólo para la Parte 1 (referencia retrospectiva)")
    ap.add_argument("--diy-window", type=int, default=5, help="velas hacia atrás (causal) para buscar señal DIY a favor")
    ap.add_argument("--min-segment-bars", type=int, default=3)
    ap.add_argument("--min-risk-ticks", type=float, default=4.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--sl-atr-mult", type=float, default=2.0)
    ap.add_argument("--tp-r-mult", type=float, default=2.0)
    ap.add_argument("--require-diy-same-bar", action="store_true")
    ap.add_argument("--require-diy-window", action="store_true", help="usa --diy-window como filtro real en la Parte 2")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    volume_available = "volume" in df.columns
    p = Params()

    print(f"Velas: {len(df)} ({df.index[0]} -> {df.index[-1]})", file=sys.stderr)
    print("=== Parte 1: correlación (kill rate / MFE por cantidad de confluencias extra) ===")
    part1_correlational(df, p, volume_available, args.pivot_flip_window, args.diy_window,
                         args.min_segment_bars, args.min_risk_ticks, args.tick_size)

    print("\n=== Parte 2: backtest real (sólo filtros causales) ===")
    diy_window_arg = args.diy_window if args.require_diy_window else None
    part2_backtest(args.csv_path, args.train_frac, args.sl_atr_mult, args.tp_r_mult, args.require_diy_same_bar, diy_window_arg)


if __name__ == "__main__":
    main()
