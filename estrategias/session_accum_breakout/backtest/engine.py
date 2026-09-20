"""
Motor de backtest para la estrategia descrita por el usuario (vista de
un trader, sin fuente exacta identificada -ver runs/README para el
detalle de la búsqueda): acumulación previa a la apertura de una sesión
+ ruptura de ese rango + seguimiento del movimiento "haciendo
promediadas" (pyramiding) con SL que se ajusta en cada promediada para
mantener el riesgo TOTAL constante, buscando un R:R 1:4 en conjunto.

Reglas tal como las describió el usuario, con los supuestos explícitos
que hicieron falta para poder programarlas (documentados también en el
README):

  1. ACUMULACIÓN: la ventana de `--lookback-hours` horas antes de la
     apertura se divide en dos partes (`--confirm-fraction`, default
     0.5): una fase de FORMACIÓN (arma el rango con el high/low de esas
     velas) y, a continuación, una fase de CONFIRMACIÓN (el resto de la
     ventana, ya con el rango de la fase anterior CONGELADO) donde se
     exige que el precio se mantenga DENTRO de ese rango hasta la
     apertura. (Probar primero comparar cada cierre contra un rango que
     todavía se estaba formando -sin congelar- invalidaba casi todos los
     días reales: cualquier consolidación hace nuevos máximos/mínimos
     todo el tiempo mientras se arma; separar formación de confirmación
     evita ese problema y sigue siendo fiel a la idea de "si se rompe
     antes de la sesión no es válido".)
  2. INVALIDACIÓN: si durante la fase de CONFIRMACIÓN el precio CIERRA
     por fuera del rango ya congelado, el setup de ese ciclo queda
     invalidado -no se opera. Si aguanta hasta la apertura, el rango
     congelado (el de la fase de formación) es el que se usa para
     buscar la ruptura real.
  3. ENTRADA: recién desde la apertura de la sesión en adelante, primera
     vela que CIERRA por fuera de ese rango -> entrada a mercado en esa
     dirección.
  4. SL INICIAL: en el extremo OPUESTO del rango (si rompe para arriba,
     SL apoyado en el low del rango; si rompe para abajo, en el high) +
     un pequeño buffer de ATR -asume que si el precio recorre todo el
     rango en contra, el setup falló. El tamaño de posición se calcula
     para arriesgar exactamente `--max-risk-usd` con este SL inicial.
  5. PROMEDIADAS (pyramiding): cada vez que el precio avanza otros
     `--add-step-atr-mult` x ATR más allá de la ÚLTIMA entrada (a favor),
     se agrega OTRA entrada de la MISMA cantidad de contratos que la
     inicial (hasta `--max-adds` agregados). Cada vez que se agrega, se
     RECALCULA el SL de TODA la posición (usando el precio promedio de
     entrada y la cantidad total) para que el riesgo total siga siendo
     exactamente `--max-risk-usd` -esto obliga a mover el SL más cerca
     del precio a medida que se agranda la posición (leer literalmente
     "el SL se ajusta cada vez que se promedia para arriesgar siempre lo
     mismo").
  6. TP: se recalcula junto con cada promediada para que, si se
     alcanza, la ganancia total sea `--tp-r-mult` (default 4.0, el 1:4
     que pidió el usuario) veces `--max-risk-usd`, sobre el precio
     promedio y la cantidad total vigentes.
  7. CIERRE: si el precio toca el SL o el TP vigentes, se cierra TODA la
     posición. También se fuerza el cierre si se llega al comienzo de la
     PRÓXIMA ventana de acumulación sin haber salido antes (evita
     arrastrar una posición de un ciclo de sesión al siguiente).

No hay guion .pine -esta estrategia se define aquí directamente en
Python porque no viene de un indicador de TradingView, sino de una
descripción de reglas.

Uso (ver optimize.py):
    python3 optimize.py datos.csv --session-open-hour 3.0 --lookback-hours 2.0
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Params:
    session_open_hour: float = 3.0    # hora de NY (0-24) en la que abre la sesión que nos interesa
    lookback_hours: float = 2.0       # ventana de acumulación previa a la apertura
    confirm_fraction: float = 0.5     # qué fracción (final) de esa ventana es fase de CONFIRMACIÓN (rango ya congelado)
    atr_len: int = 14
    sl_buffer_atr: float = 0.10       # buffer sobre el extremo opuesto del rango para el SL inicial
    add_step_atr_mult: float = 1.0    # cuánto debe avanzar el precio (en ATR) más allá de la última entrada para promediar
    max_adds: int = 2                 # cantidad de promediadas ADICIONALES a la entrada inicial (0 = sin pyramiding)
    tp_r_mult: float = 4.0            # objetivo TOTAL en múltiplos del riesgo fijo (el "RR 1:4" pedido)
    max_risk_usd: float = 750.0       # riesgo total fijo de la posición completa (se mantiene constante al promediar)
    point_value_usd: float = 2.0
    max_qty: int = 40
    commission_round_turn_usd: float = 3.50
    slippage_ticks: float = 1.0
    tick_size: float = 0.25
    session_tz: str = "America/New_York"


def _wilder_rma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) >= length:
        prev = values[:length].mean()
        out[length - 1] = prev
        for i in range(length, len(values)):
            prev = (prev * (length - 1) + values[i]) / length
            out[i] = prev
    return out


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> np.ndarray:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return _wilder_rma(tr.to_numpy(dtype=float), length)


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)

    atr = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)
    slip = p.slippage_ticks * p.tick_size

    local = ts.tz_convert(p.session_tz)
    hour_frac = (local.hour + local.minute / 60.0 + local.second / 3600.0).to_numpy()
    # mod_hours = 0 exactamente en la apertura de la sesión, crece hasta 24
    # a lo largo del ciclo -evita tener que trackear "día calendario" y
    # maneja sola los ciclos que cruzan medianoche.
    mod_hours = (hour_frac - p.session_open_hour) % 24.0
    thresh_start = 24.0 - p.lookback_hours                              # inicio de la fase de FORMACIÓN
    thresh_confirm = 24.0 - p.lookback_hours * p.confirm_fraction       # inicio de la fase de CONFIRMACIÓN

    range_low, range_high = np.inf, -np.inf
    fixed_low, fixed_high = np.nan, np.nan
    accum_invalid = False
    cycle_valid = False
    cycle_range_low = cycle_range_high = np.nan
    traded_this_cycle = False

    open_pos = False
    is_long = False
    entry_prices: list[float] = []
    qty_per_leg = 0
    sl = tp = np.nan
    entry_bar = None

    trades = []

    for i in range(n):
        mh = mod_hours[i]
        prev_mh = mod_hours[i - 1] if i > 0 else mh
        entering_accum = prev_mh < thresh_start <= mh
        entering_confirm = prev_mh < thresh_confirm <= mh
        leaving_accum = (prev_mh >= thresh_start) and (mh < thresh_start)
        if i == 0:
            entering_accum = mh >= thresh_start
            entering_confirm = mh >= thresh_confirm
            leaving_accum = mh < thresh_start

        if entering_accum:
            range_low, range_high = np.inf, -np.inf
            fixed_low, fixed_high = np.nan, np.nan
            accum_invalid = False

        in_forming = thresh_start <= mh < thresh_confirm
        in_confirming = mh >= thresh_confirm

        if entering_confirm:
            # se congela el rango armado durante la fase de formación
            fixed_low, fixed_high = range_low, range_high

        if in_forming and not accum_invalid:
            range_low = min(range_low, l[i])
            range_high = max(range_high, h[i])
        elif in_confirming and not accum_invalid and not np.isnan(fixed_low):
            if c[i] > fixed_high or c[i] < fixed_low:
                accum_invalid = True  # el precio rompió el rango ya congelado antes de que abra la sesión

        if leaving_accum:
            traded_this_cycle = False
            cycle_valid = (not accum_invalid) and (not np.isnan(fixed_low)) and (fixed_high > fixed_low)
            cycle_range_low, cycle_range_high = fixed_low, fixed_high
            if open_pos:
                # fuerza cierre de una posición que quedó abierta de un ciclo anterior
                exit_price = c[i] - slip if is_long else c[i] + slip
                total_qty = qty_per_leg * len(entry_prices)
                avg_entry = float(np.mean(entry_prices))
                pnl = (exit_price - avg_entry) * total_qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * total_qty
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "avg_entry": avg_entry, "exit": exit_price, "sl": sl, "tp": tp,
                    "n_legs": len(entry_prices), "qty": total_qty, "pnl_usd": pnl,
                    "r_multiple": pnl / p.max_risk_usd, "reason": "CYCLE_END",
                    "bars_held": i - entry_bar,
                })
                open_pos = False
                entry_prices = []

        if in_forming or in_confirming:
            continue  # durante la acumulación no se opera ni se gestiona posición

        # ---- fase "post apertura": gestionar posición abierta o buscar ruptura ----
        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)
            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
            elif hit_tp:
                exit_price = tp
                exit_reason = "TP"

            if exit_price is not None:
                total_qty = qty_per_leg * len(entry_prices)
                avg_entry = float(np.mean(entry_prices))
                pnl = (exit_price - avg_entry) * total_qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * total_qty
                trades.append({
                    "entry_time": ts[entry_bar], "exit_time": ts[i],
                    "direction": "long" if is_long else "short",
                    "avg_entry": avg_entry, "exit": exit_price, "sl": sl, "tp": tp,
                    "n_legs": len(entry_prices), "qty": total_qty, "pnl_usd": pnl,
                    "r_multiple": pnl / p.max_risk_usd, "reason": exit_reason,
                    "bars_held": i - entry_bar,
                })
                open_pos = False
                entry_prices = []
            elif len(entry_prices) - 1 < p.max_adds and not np.isnan(atr[i]) and atr[i] > 0:
                last_entry = entry_prices[-1]
                step = p.add_step_atr_mult * atr[i]
                should_add = (c[i] >= last_entry + step) if is_long else (c[i] <= last_entry - step)
                if should_add:
                    add_price = c[i] + slip if is_long else c[i] - slip
                    new_entries = entry_prices + [add_price]
                    new_total_qty = qty_per_leg * len(new_entries)
                    new_avg = float(np.mean(new_entries))
                    per_unit_risk = p.max_risk_usd / (new_total_qty * p.point_value_usd)
                    new_sl = new_avg - per_unit_risk if is_long else new_avg + per_unit_risk
                    safe = (new_sl < c[i]) if is_long else (new_sl > c[i])
                    if safe:
                        entry_prices = new_entries
                        sl = new_sl
                        per_unit_tp = (p.tp_r_mult * p.max_risk_usd) / (new_total_qty * p.point_value_usd)
                        tp = new_avg + per_unit_tp if is_long else new_avg - per_unit_tp

        if not open_pos and cycle_valid and not traded_this_cycle:
            if c[i] > cycle_range_high:
                is_long = True
            elif c[i] < cycle_range_low:
                is_long = False
            else:
                continue
            atr_i = atr[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            entry_signal_price = c[i]
            entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
            sl_initial = (cycle_range_low - p.sl_buffer_atr * atr_i) if is_long else (cycle_range_high + p.sl_buffer_atr * atr_i)
            risk_pts = abs(entry_price - sl_initial)
            if risk_pts <= 0:
                continue
            qty_per_leg = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd)))
            if qty_per_leg <= 0:
                continue
            entry_prices = [entry_price]
            sl = sl_initial
            tp = entry_price + p.tp_r_mult * risk_pts if is_long else entry_price - p.tp_r_mult * risk_pts
            entry_bar = i
            open_pos = True
            traded_this_cycle = True

    trades_df = pd.DataFrame(trades)
    return trades_df, summarize(trades_df)


def summarize(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"trades": 0, "win_rate": np.nan, "profit_factor": np.nan, "expectancy_r": np.nan,
                "net_pnl_usd": 0.0, "max_drawdown_usd": 0.0, "avg_bars_held": np.nan, "avg_legs": np.nan}
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
        "avg_legs": trades_df["n_legs"].mean(),
    }
