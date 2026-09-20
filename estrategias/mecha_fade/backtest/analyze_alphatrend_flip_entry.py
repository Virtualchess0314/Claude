"""
Backtest de una entrada DISTINTA a la mecha_fade: en vez de esperar el
fade (mecha + cierre adentro), entra directo al FLIP de régimen de
AlphaTrend, a favor de la nueva tendencia.

Ojo con la expectativa: analyze_post_kill_runup.py mostró que "matar" el
pivot (nunca volverlo a tocar) es la firma de los tramos CHICOS (mediana
~0.5-0.6R), y que los tramos grandes (mediana ~3.5-4R) son los que
retestean el pivot antes de seguir. Acá no filtramos por eso -se entra a
TODOS los flips, dejando que el stop/TP reales decidan quién gana y
quién pierde- así que el resultado es la mezcla de ambos casos, no sólo
el caso "lindo" de un único ejemplo visual.

Mecánica (reutiliza el modelo de costos de engine.Params para que el
resultado sea comparable con el resto de la estrategia):
  - Señal: flip de AlphaTrend, definido por PENDIENTE de la línea
    (AlphaTrend[i] vs AlphaTrend[i-2] -así colorea la nube el indicador
    público real, ver detect_alphatrend_flips), confirmado de forma
    CAUSAL: recién se toma como señal cuando el nuevo régimen se
    sostuvo `--confirm-bars` velas seguidas (default 1 = entrada
    inmediata en la vela del flip crudo, sin demora).
  - Entrada: a mercado al cierre de la vela de confirmación, a favor del
    nuevo régimen.
  - SL: ATR puro (entry -/+ `--sl-atr-mult` x ATR). NO se apoya en el
    Pivot Point SuperTrend: con flips ahora limpios y espaciados
    (~30-40 velas de duración típica), el pivot vigente frecuentemente
    queda del lado equivocado del precio (su propio trend interno no
    cambió al mismo tiempo que AlphaTrend) -da riesgo negativo/sin
    sentido en ~46% de los flips. Ver runs/2026-09-19_alphatrend_regime_fix.txt.
  - Salida: TP a un múltiplo de R (--tp-r-mult), trailing sobre
    AlphaTrend/Pivot por VALOR de línea (--trailing-exit-source at/piv,
    ruidoso, no recomendado) o por RÉGIMEN (--trailing-exit-source
    regime, recomendado: cierra recién cuando el mismo criterio de
    pendiente vuelve a flipear en contra), o cierre de sesión (EOD).
  - Costos: comisión + slippage igual que engine.Params.
  - Partición train/test 70/30 en el tiempo, igual que optimize.py.

Uso:
    python3 analyze_alphatrend_flip_entry.py datos.csv
    python3 analyze_alphatrend_flip_entry.py datos.csv --sl-atr-mult 3.0 --trailing-exit-source regime
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from analyze_alphatrend_kills_pivot import detect_alphatrend_flips
from data import load_csv
from engine import Params, _within_session, alpha_trend, detect_crt, pivot_point_supertrend, summarize, wilder_atr


def crt_near_flip(crt: np.ndarray, flip_i: int, direction: float, window: int) -> bool:
    lo = max(0, flip_i - window + 1)
    return bool(np.any(crt[lo:flip_i + 1] == direction))


def causal_confirmed_flips(regime: np.ndarray, confirm_bars: int) -> np.ndarray:
    """
    Alternativa CAUSAL a analyze_alphatrend_kills_pivot.filter_genuine_flips
    (que decide si un flip es "genuino" mirando cuánto dura el tramo
    completo hasta el PRÓXIMO flip -información que no existe todavía en
    el momento de entrar). Acá el flip recién se confirma cuando el nuevo
    régimen se sostuvo `confirm_bars` velas seguidas contando desde la del
    flip -sólo usa información ya conocida en cada vela. confirm_bars=1 =
    entrada inmediata en la vela del flip crudo, sin ninguna demora.
    """
    n = len(regime)
    flips = []
    prev = None
    flip_bar = None
    for i in range(n):
        if np.isnan(regime[i]):
            prev = None
            flip_bar = None
            continue
        if prev is not None and regime[i] != prev:
            flip_bar = i
        if flip_bar is not None and i - flip_bar == confirm_bars - 1:
            flips.append(i)
        prev = regime[i]
    return np.array(flips, dtype=int)


def pivot_confirmation_bars(regime: np.ndarray, piv_trend: np.ndarray) -> np.ndarray:
    """
    Señal alternativa a causal_confirmed_flips(): en vez de entrar al
    flip crudo de AlphaTrend, esperar a que el PIVOT MISMO confirme -el
    pivot viejo (de color contrario) muere y nace uno nuevo del color de
    la tendencia actual (piv_trend pasa a igualar regime). Es justo el
    evento que runs/2026-09-19_pivot_flip_definition_fix.txt encontró
    asociado casi perfectamente a los recorridos grandes (mediana
    4.5-5R, 99-100% llega a 1R) vs los chicos (mediana 0.5-0.65R) del
    pivot que nunca confirma. Es causal por construcción: sólo usa el
    valor de piv_trend en la vela actual y la anterior.
    """
    n = len(regime)
    bars = []
    for i in range(1, n):
        if np.isnan(regime[i]) or np.isnan(piv_trend[i]) or np.isnan(piv_trend[i - 1]):
            continue
        if piv_trend[i] != piv_trend[i - 1] and piv_trend[i] == regime[i]:
            bars.append(i)
    return np.array(bars, dtype=int)


def simulate_pivot_confirmation_entries(df: pd.DataFrame, p: Params, sl_buffer_atr: float) -> tuple[pd.DataFrame, dict]:
    """
    Entra recién cuando el Pivot Point SuperTrend confirma la tendencia
    actual de AlphaTrend (ver pivot_confirmation_bars). En ese momento
    el pivot recién nacido SÍ queda del lado correcto del precio (a
    diferencia del pivot viejo al momento del flip crudo -ver
    runs/2026-09-19_alphatrend_regime_fix.txt), así que el SL se apoya
    directo en él (con un pequeño buffer de ATR), en vez de usar ATR
    puro.
    """
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    regime, _ = detect_alphatrend_flips(df, p, volume_available)
    flips = pivot_confirmation_bars(regime, piv_trend)
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
            hit_trail = hit_tp = False
            if p.trailing_exit_source == "regime":
                if not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            elif p.trailing_exit_source == "pivot_reflip":
                # sale apenas el PIVOT (no la nube) vuelve a flipear en contra
                if not np.isnan(piv_trend[i]):
                    hit_trail = (piv_trend[i] == -1.0) if is_long else (piv_trend[i] == 1.0)
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
            buffer = sl_buffer_atr * atr_i
            if is_long:
                sl = level - buffer
                risk_pts = entry_price - sl
                tp = entry_price + risk_pts * p.tp_r_mult
            else:
                sl = level + buffer
                risk_pts = sl - entry_price
                tp = entry_price - risk_pts * p.tp_r_mult
            if risk_pts <= 0:
                continue  # no debería pasar (el pivot ya confirmó del lado correcto), pero por las dudas
            qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
            if qty > 0:
                open_pos = True
                dtrades += 1

    trades_df = pd.DataFrame(trades)
    return trades_df, summarize(trades_df, volume_available)


def simulate_flip_entries(df: pd.DataFrame, p: Params, confirm_bars: int, sl_atr_mult: float,
                           require_opposite_color_pivot: bool = False,
                           max_pivot_distance_atr: float | None = None,
                           crt_filter: str | None = None, crt_window: int = 3) -> tuple[pd.DataFrame, dict]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    piv_line, piv_trend = pivot_point_supertrend(df, p)
    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    crt = detect_crt(df) if crt_filter is not None else None
    slip = p.slippage_ticks * p.tick_size

    regime, _flips_raw = detect_alphatrend_flips(df, p, volume_available)
    flips = causal_confirmed_flips(regime, confirm_bars)
    flip_set = set(flips.tolist())

    last_day = None
    dtrades = 0
    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0
    pivot_confirmed = False
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
            hit_trail = False
            hit_tp = False
            if p.trailing_exit_source == "regime":
                # Salida por el MISMO criterio de régimen que la entrada
                # (pendiente de AlphaTrend, no "cierre vs línea" -ese
                # criterio es ruidoso, ver detect_alphatrend_flips):
                # cierra apenas el régimen vuelve a flipear en contra.
                if not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            elif p.trailing_exit_source == "pivot_confirms":
                # Secuencia del usuario: entrar al flip de AlphaTrend con
                # el pivot todavía del color viejo, y salir/tomar TP
                # recién cuando el PIVOT confirma (flipea a favor de la
                # posición) -no antes (no es TP fijo) ni mucho después
                # (no es esperar a que la nube misma se revierta).
                if not np.isnan(piv_trend[i]):
                    hit_trail = (piv_trend[i] == 1.0) if is_long else (piv_trend[i] == -1.0)
            elif p.trailing_exit_source == "pivot_confirms_then_trail":
                # Variante "o dejarlo correr" de la secuencia del usuario:
                # cuando el pivot confirma, en vez de cerrar, se mueve el
                # SL a breakeven (entry_price) y se deja correr hasta que
                # la nube misma se revierta (regime).
                if not pivot_confirmed and not np.isnan(piv_trend[i]):
                    if (piv_trend[i] == 1.0) if is_long else (piv_trend[i] == -1.0):
                        pivot_confirmed = True
                        sl = entry_price
                if pivot_confirmed and not np.isnan(regime[i]):
                    hit_trail = (regime[i] == -1.0) if is_long else (regime[i] == 1.0)
            elif p.trailing_exit_source is not None:
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
                # usar el riesgo ORIGINAL de la entrada, no el sl actual
                # (puede haberse movido a breakeven con pivot_confirms_then_trail,
                # lo que daría riesgo 0 / r_multiple corrupto si se recalcula acá)
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "entry": entry_price, "exit": exit_price, "sl": sl, "reason": exit_reason,
                    "qty": qty, "pnl_usd": pnl,
                    "r_multiple": pnl / (entry_risk_pts * qty * p.point_value_usd) if entry_risk_pts and entry_risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        in_entry_window = True
        if p.entry_start_hour is not None and p.entry_end_hour is not None:
            hour_frac = local.hour + local.minute / 60.0
            if p.entry_start_hour <= p.entry_end_hour:
                in_entry_window = p.entry_start_hour <= hour_frac < p.entry_end_hour
            else:
                in_entry_window = hour_frac >= p.entry_start_hour or hour_frac < p.entry_end_hour

        if not open_pos and i in flip_set and in_sess and in_entry_window and dtrades < p.max_trades_per_day:
            atr_i = atr_risk[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            if require_opposite_color_pivot and (np.isnan(piv_trend[i]) or piv_trend[i] == regime[i]):
                continue  # el pivot ya es del mismo color que la nube nueva -no cuenta (ver post_kill_runup)
            if max_pivot_distance_atr is not None:
                # hipótesis del usuario: si el pivot está exageradamente
                # lejos del precio, es más probable que otro flip de
                # AlphaTrend "resetee" el tramo antes de que el precio lo
                # busque -ver runs/2026-09-20_pivot_distance_vs_kill.txt
                if np.isnan(piv_line[i]):
                    continue
                distance_atr = abs(c[i] - piv_line[i]) / atr_i
                if distance_atr > max_pivot_distance_atr:
                    continue
            if crt_filter is not None:
                # metodología ICT CRT (Candle Range Theory, ver
                # engine.detect_crt): ¿hubo una manipulación/barrido de
                # liquidez seguida de reclamo a favor de la nueva
                # tendencia en las últimas `crt_window` velas? -ver
                # runs/2026-09-20_crt_vs_kill.txt
                has_crt = crt_near_flip(crt, i, regime[i], crt_window)
                if crt_filter == "require" and not has_crt:
                    continue
                if crt_filter == "exclude" and has_crt:
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
                pivot_confirmed = False
                entry_risk_pts = risk_pts

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
    ap.add_argument("--sl-atr-mult", type=float, default=2.0,
                     help="SL = entry -/+ sl_atr_mult x ATR (no depende del pivot); con --pivot-confirmation es el buffer sobre el pivot recién nacido (usar valores chicos, ej 0.1-0.5)")
    ap.add_argument("--tp-r-mult", type=float, default=2.0)
    ap.add_argument("--trailing-exit-source",
                     choices=["at", "piv", "regime", "pivot_reflip", "pivot_confirms", "pivot_confirms_then_trail"],
                     default=None,
                     help="'at'/'piv': cierre cruza la línea (ruidoso). 'regime': espera al próximo flip real de nube. "
                          "'pivot_confirms': sale cuando el PIVOT flipea a favor de la posición. "
                          "'pivot_confirms_then_trail': al confirmar, mueve el SL a breakeven y deja correr hasta que la nube se revierta. "
                          "'pivot_reflip' (sólo con --pivot-confirmation): sale cuando el PIVOT vuelve a flipear en contra")
    ap.add_argument("--confirm-bars", type=int, default=1,
                     help="velas que el nuevo régimen debe sostenerse antes de confirmar la entrada (1=inmediato, causal)")
    ap.add_argument("--require-opposite-color-pivot", action="store_true",
                     help="sólo entra si, al momento del flip, el Pivot Point SuperTrend es de color CONTRARIO a la nube nueva (ver runs/2026-09-19_post_kill_runup.txt)")
    ap.add_argument("--max-pivot-distance-atr", type=float, default=None,
                     help="descarta la entrada si el pivot está a más de N x ATR del precio en el momento del flip (ver runs/2026-09-20_pivot_distance_vs_kill.txt)")
    ap.add_argument("--entry-start-hour", type=float, default=None, help="hora de NY (0-24) desde la que se permite ABRIR una entrada nueva")
    ap.add_argument("--entry-end-hour", type=float, default=None, help="hora de NY (0-24) hasta la que se permite ABRIR una entrada nueva")
    ap.add_argument("--crt-filter", choices=["require", "exclude"], default=None,
                     help="'require': sólo entra si hubo un CRT (ICT Candle Range Theory) a favor de la nueva tendencia en las últimas --crt-window velas. 'exclude': lo contrario. Ver runs/2026-09-20_crt_vs_kill.txt")
    ap.add_argument("--crt-window", type=int, default=3)
    ap.add_argument("--pivot-confirmation", action="store_true",
                     help="en vez de entrar al flip crudo de AlphaTrend, esperar a que el PIVOT confirme (nazca del color de la tendencia actual) -ver runs/2026-09-19_pivot_flip_definition_fix.txt")
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--max-risk-usd", type=float, default=750.0)
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
               atr_len=args.atr_len, tp_r_mult=args.tp_r_mult,
               trailing_exit_source=args.trailing_exit_source,
               max_risk_usd=args.max_risk_usd, point_value_usd=args.point_value_usd, max_qty=args.max_qty,
               max_trades_per_day=args.max_trades_per_day,
               commission_round_turn_usd=args.commission_round_turn_usd, slippage_ticks=args.slippage_ticks,
               tick_size=args.tick_size, use_session=not args.no_session_close,
               entry_start_hour=args.entry_start_hour, entry_end_hour=args.entry_end_hour)

    split_at = int(len(df) * args.train_frac)
    df_train, df_test = df.iloc[:split_at], df.iloc[split_at:]
    print(f"Datos: {len(df)} velas · train={len(df_train)} ({df_train.index[0]} -> {df_train.index[-1]}) "
          f"· test={len(df_test)} ({df_test.index[0]} -> {df_test.index[-1]})", file=sys.stderr)

    if args.pivot_confirmation:
        _, s_train = simulate_pivot_confirmation_entries(df_train, p, args.sl_atr_mult)
        _, s_test = simulate_pivot_confirmation_entries(df_test, p, args.sl_atr_mult)
    else:
        _, s_train = simulate_flip_entries(df_train, p, args.confirm_bars, args.sl_atr_mult, args.require_opposite_color_pivot, args.max_pivot_distance_atr, args.crt_filter, args.crt_window)
        _, s_test = simulate_flip_entries(df_test, p, args.confirm_bars, args.sl_atr_mult, args.require_opposite_color_pivot, args.max_pivot_distance_atr, args.crt_filter, args.crt_window)

    for label, s in [("TRAIN", s_train), ("TEST", s_test)]:
        print(f"\n{label}: trades={s['trades']}  win_rate={s['win_rate']*100:.1f}%  "
              f"PF={s['profit_factor']:.2f}  expectancy_r={s['expectancy_r']:.3f}  "
              f"net_pnl_usd={s['net_pnl_usd']:.0f}  avg_bars_held={s['avg_bars_held']:.1f}")


if __name__ == "__main__":
    main()
