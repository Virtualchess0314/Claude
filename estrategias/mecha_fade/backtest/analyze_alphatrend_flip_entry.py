"""
Backtest de una entrada DISTINTA a la mecha_fade: en vez de esperar el
fade (mecha + cierre adentro), entra directo al FLIP de régimen de
AlphaTrend, a favor de la nueva tendencia. El SL se apoya en el nivel del
Pivot Point SuperTrend vigente al momento del flip (el nivel que el
precio está a punto de romper), igual que en analyze_post_kill_runup.py.

Ojo con la expectativa: analyze_post_kill_runup.py mostró que "matar" el
pivot (nunca volverlo a tocar) es la firma de los tramos CHICOS (mediana
~0.5R), y que los tramos grandes (mediana ~2R) son los que retestean el
pivot antes de seguir. Acá no filtramos por eso -se entra a TODOS los
flips genuinos, dejando que el stop/TP reales decidan quién gana y quién
pierde- así que el resultado es la mezcla de ambos casos, no sólo el caso
"lindo" de un único ejemplo visual.

Mecánica exacta (igual que engine.simulate(), reutilizando sus mismas
funciones y modelo de costos, para que el resultado sea comparable con
el resto de la estrategia):
  - Señal: flip genuino de AlphaTrend (cruce de cierre contra la línea,
    tramo resultante >= --min-segment-bars velas -filtra whipsaw).
  - Entrada: a mercado al cierre de la vela del flip, a favor del nuevo
    régimen.
  - SL: nivel del Pivot Point SuperTrend en ese momento, +/- buffer de
    ATR (--sl-buffer-atr).
  - Salida: TP a un múltiplo de R (--tp-r-mult) o trailing sobre
    AlphaTrend/Pivot (--trailing-exit-source), cierre de sesión (EOD),
    lo que llegue primero.
  - Costos: comisión + slippage igual que engine.Params.
  - Partición train/test 70/30 en el tiempo, igual que optimize.py.

Uso:
    python3 analyze_alphatrend_flip_entry.py datos.csv
    python3 analyze_alphatrend_flip_entry.py datos.csv --trailing-exit-source piv --sl-buffer-atr 0.5
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips, filter_genuine_flips
from data import load_csv
from engine import Params, _within_session, alpha_trend, pivot_point_supertrend, summarize, wilder_atr


def simulate_flip_entries(df: pd.DataFrame, p: Params, min_segment_bars: int, min_risk_ticks: float) -> tuple[pd.DataFrame, dict]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    piv_line, _ = pivot_point_supertrend(df, p)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    regime, flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = filter_genuine_flips(regime, flips_raw, n, min_segment_bars)
    flip_set = set(flips.tolist())

    last_day = None
    dtrades = 0
    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0

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
            hit_trail = False
            hit_tp = False
            if p.trailing_exit_source is not None:
                trail_line = alpha[i] if p.trailing_exit_source == "at" else piv_line[i]
                if not np.isnan(trail_line):
                    hit_trail = (c[i] < trail_line) if is_long else (c[i] > trail_line)
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
                risk_pts = abs(entry_price - sl)
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "entry": entry_price, "exit": exit_price, "sl": sl, "reason": exit_reason,
                    "qty": qty, "pnl_usd": pnl,
                    "r_multiple": pnl / (risk_pts * qty * p.point_value_usd) if risk_pts and risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        if not open_pos and i in flip_set and in_sess and dtrades < p.max_trades_per_day:
            level = piv_line[i]
            atr_i = atr_risk[i]
            if np.isnan(level) or np.isnan(atr_i) or atr_i <= 0:
                continue
            is_long = regime[i] == 1.0
            entry_signal_price = c[i]
            entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
            entry_bar = i
            buffer = p.sl_buffer_atr * atr_i
            if is_long:
                sl = level - buffer
                risk_pts = entry_price - sl
                tp = entry_price + risk_pts * p.tp_r_mult
            else:
                sl = level + buffer
                risk_pts = sl - entry_price
                tp = entry_price - risk_pts * p.tp_r_mult
            if risk_pts < min_risk_ticks * p.tick_size:
                continue
            qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
            if qty > 0:
                open_pos = True
                dtrades += 1

    trades_df = pd.DataFrame(trades)
    return trades_df, summarize(trades_df, volume_available)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--at-period", type=int, default=14)
    ap.add_argument("--at-mult", type=float, default=1.0)
    ap.add_argument("--piv-period", type=int, default=2)
    ap.add_argument("--piv-atr-period", type=int, default=10)
    ap.add_argument("--piv-atr-factor", type=float, default=3.0)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--sl-buffer-atr", type=float, default=0.10)
    ap.add_argument("--tp-r-mult", type=float, default=2.0)
    ap.add_argument("--trailing-exit-source", choices=["at", "piv"], default=None)
    ap.add_argument("--min-segment-bars", type=int, default=3)
    ap.add_argument("--min-risk-ticks", type=float, default=4.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--max-risk-usd", type=float, default=150.0)
    ap.add_argument("--point-value-usd", type=float, default=2.0)
    ap.add_argument("--max-qty", type=int, default=40)
    ap.add_argument("--max-trades-per-day", type=int, default=999)
    ap.add_argument("--commission-round-turn-usd", type=float, default=3.50)
    ap.add_argument("--slippage-ticks", type=float, default=1.0)
    ap.add_argument("--no-session-close", action="store_true")
    args = ap.parse_args()

    df = load_csv(args.csv_path)
    p = Params(at_period=args.at_period, at_mult=args.at_mult, at_use_volume=False,
               piv_period=args.piv_period, piv_atr_period=args.piv_atr_period, piv_atr_factor=args.piv_atr_factor,
               atr_len=args.atr_len, sl_buffer_atr=args.sl_buffer_atr, tp_r_mult=args.tp_r_mult,
               trailing_exit_source=args.trailing_exit_source,
               max_risk_usd=args.max_risk_usd, point_value_usd=args.point_value_usd, max_qty=args.max_qty,
               max_trades_per_day=args.max_trades_per_day,
               commission_round_turn_usd=args.commission_round_turn_usd, slippage_ticks=args.slippage_ticks,
               tick_size=args.tick_size, use_session=not args.no_session_close)

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    print(f"Datos: {len(df)} velas · train={len(df_train)} ({df_train.index[0]} -> {df_train.index[-1]}) "
          f"· test={len(df_test)} ({df_test.index[0]} -> {df_test.index[-1]})", file=sys.stderr)

    _, s_train = simulate_flip_entries(df_train, p, args.min_segment_bars, args.min_risk_ticks)
    _, s_test = simulate_flip_entries(df_test, p, args.min_segment_bars, args.min_risk_ticks)

    for label, s in [("TRAIN", s_train), ("TEST", s_test)]:
        print(f"\n{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  "
              f"net_pnl_usd={s['net_pnl_usd']:.0f}  avg_bars_held={s['avg_bars_held']:.1f}")


if __name__ == "__main__":
    main()
