"""
Motor de backtest para Mecha Fade — réplica bar-a-bar de la lógica de
estrategias/mecha_fade/mecha_fade.pine, para barrer parámetros sin
depender del Strategy Tester de TradingView.

Estrategia (según la describió el usuario): 3 indicadores -AlphaTrend,
Pivot y "DIY"- actúan cada uno como nivel de soporte/resistencia. Cuando
una vela mete una mecha por FUERA de un nivel pero CIERRA de nuevo
adentro (barrido de liquidez / falso quiebre), se entra a MERCADO en el
cierre de esa vela buscando el movimiento opuesto (fade).

Definición de los 3 indicadores (confirmada con el usuario tras compartir
el código del indicador "DIY Custom Strategy Builder [ZP]" y una captura
del gráfico real):

  - AlphaTrend: indicador externo, no está en el script "DIY". Fórmula
    pública estándar (ratchet sobre ATR/RSI o ATR/MFI).
  - Pivot: NO son floor pivots de piso (primer intento, descartado) sino
    el indicador público "Pivot Point SuperTrend" -pivotes de swing
    (ta.pivothigh/low) promediados en una línea "center", envueltos en un
    trailing stop tipo SuperTrend sobre ATR. Es la línea roja/verde
    escalonada que el usuario identificó en el gráfico (cambia de color
    cuando el precio la cruza). Replicado en `pivot_point_supertrend()`.
  - DIY: el usuario confirmó que de ese script sólo usa la función de
    "Supply/Demand Zone" (las cajas soporte/resistencia ancladas a los
    últimos swing high/low, con un buffer de ATR(50) y lógica de ruptura/
    BOS) -no el motor de señales de ~35 indicadores líder + confirmaciones
    que trae el resto del script. Replicado en `supply_demand_zones()`.
    El "punto rojo" que se ve en el gráfico es el marcador de POI (punto
    medio) de una zona recién formada -aparece como un punto porque la
    caja nace angosta (sólo `diy_swing_length` velas de ancho).

⚠️ PENDIENTE DE CONFIRMAR — 2 huecos que siguen sin definición del
usuario, con un supuesto explícito documentado para tener un motor
completo y testeable desde ya:

  1. Confluencia: no está claro si los 3 indicadores deben coincidir a
     la vez o alcanza con cualquiera. Default = cualquiera
     (`confluence_need=1`), configurable 1..3.
  2. Regla de salida: no especificada. Se usó SL más allá del extremo de
     la mecha que disparó la señal (+ buffer en ATR) y TP a un múltiplo R
     de ese riesgo -ambos parametrizables (`sl_buffer_atr`, `tp_r_mult`)
     para poder barrerlos en optimize.py.

Simplificación documentada sobre la zona Supply/Demand: el script
original mantiene un historial de hasta 20 zonas por lado y permite
varias activas en simultáneo (mientras no se solapen). Acá se sigue sólo
la ZONA MÁS RECIENTE por lado (se reemplaza al aparecer un nuevo swing
no solapado, se desactiva al romperse -mismo criterio BOS: cierre cruza
el borde de la zona). Para el propósito de esta señal (fade de mecha
contra el soporte/resistencia más cercano) alcanza con la zona vigente;
no se replicó el historial completo de zonas.

Reglas replicadas 1:1 con el .pine:
  - ATR/RSI de Wilder (misma fórmula que ta.atr/ta.rsi).
  - Confluencia configurable: `confluence_need` indicadores de 3 deben
    coincidir en la misma dirección en la misma vela.
  - Si en la misma vela hay señal long Y short (indicadores contradictorios),
    se descarta la vela -criterio conservador, no hay forma de saber cuál
    "ganaría" intrabar con datos OHLC.
  - Entrada a MERCADO en el cierre de la vela de señal (con slippage,
    porque es orden de mercado). SL es un stop (con slippage). TP es un
    límite (sin slippage).
  - Tamaño de posición ajustado para arriesgar siempre `max_risk_usd`
    (mismo criterio que ifvg_sniper), tope en `max_qty` contratos.
  - Corte de sesión con cierre forzado, tope opcional de operaciones/día.
  - Comisión round-turn por contrato (una vez por operación cerrada).

Simplificación documentada: si en la misma vela se tocan SL y TP, se
asume que el SL se ejecuta primero (conservador, igual que en los otros
dos backtesters del repo).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Params:
    # AlphaTrend
    at_period: int = 14
    at_mult: float = 1.0
    at_use_volume: bool = False  # si no hay columna volume, se fuerza False igual

    # Pivot (Pivot Point SuperTrend)
    piv_period: int = 2         # left=right para ta.pivothigh/pivotlow (prd)
    piv_atr_period: int = 10    # ATR del trailing stop (Pd)
    piv_atr_factor: float = 3.0  # multiplicador del ATR (Factor)

    session_tz: str = "America/New_York"

    # DIY = Supply/Demand Zone del indicador ZP (ver docstring del módulo)
    diy_swing_length: int = 10
    diy_atr_len: int = 50
    diy_box_width: float = 2.5       # buffer de la zona = ATR(diy_atr_len) * box_width/10
    diy_overlap_atr_mult: float = 2.0  # no reemplaza la zona activa si el nuevo swing cae más cerca que esto

    # Confluencia: cuántos de los 3 indicadores deben coincidir (1..3)
    confluence_need: int = 1

    # Aislar un solo indicador para comparar su calidad individual
    # ('at'/'piv'/'diy'/None=los 3). Herramienta de análisis -no forma
    # parte de la estrategia original, ignora confluence_need cuando
    # está seteado.
    only_source: str | None = None

    # Aislar una sola dirección ('long'/'short'/None=ambas). Herramienta
    # de análisis -por ej. DIY mostró asimetría consistente (demanda/
    # soporte mejor que oferta/resistencia en las 5 temporalidades),
    # esto permite testear "sólo long" de forma limpia sin que las
    # señales de la otra dirección interfieran en la secuencia de
    # trades (a diferencia de filtrar el trades_df después de simular).
    only_direction: str | None = None

    # Filtro de frescura: sólo cuenta la PRIMERA mecha que testea cada
    # nivel/zona desde que "nació" -para AlphaTrend/Pivot, desde el
    # último flip de régimen (cambio de lado del precio); para DIY,
    # desde que se formó la zona vigente. Repeticiones contra el mismo
    # nivel/zona dentro de la misma vida se ignoran (ya "gastó" su
    # liquidez). Default off (comportamiento original).
    fresh_only: bool = False

    # Riesgo / salida
    atr_len: int = 14
    sl_buffer_atr: float = 0.10
    tp_r_mult: float = 1.5
    max_risk_usd: float = 750.0
    point_value_usd: float = 2.0
    max_qty: int = 40

    # Salida por TRAILING en vez de TP fijo ('at'/'piv'/None). Motivado
    # por el ejemplo del usuario: SL chico en la entrada, pero en vez de
    # cerrar en un múltiplo de R fijo, se queda montado mientras el
    # indicador elegido siga actuando de soporte (long) o resistencia
    # (short) -se sale recién cuando el precio cierra del otro lado
    # (mismo criterio de "flip de régimen" que en
    # analyze_alphatrend_kills_pivot.py). El SL original se mantiene
    # como piso de seguridad (nunca se relaja, sólo el TP se reemplaza).
    # tp_r_mult se ignora por completo si esto está seteado.
    trailing_exit_source: str | None = None

    # Sesión / control diario
    use_session: bool = True
    session_close_hour: int = 16
    session_close_minute: int = 45
    max_trades_per_day: int = 999  # 999 = sin límite práctico

    # Filtro de ventana horaria de ENTRADA (hora de NY, 0-24). None =
    # sin filtro. No afecta salidas de posiciones ya abiertas, sólo si
    # se permite ABRIR una nueva. Motivado por
    # runs/2026-09-17_session_performance.txt: la sesión NY (12-17 ET)
    # rinde sistemáticamente mejor que el resto en las 5 temporalidades
    # probadas, y el overlap Londres/NY (08-12 ET) sistemáticamente peor.
    entry_start_hour: float | None = None
    entry_end_hour: float | None = None

    # Costos reales
    commission_round_turn_usd: float = 3.50  # confirmado Tradovate/Tradeify
    slippage_ticks: float = 1.0
    tick_size: float = 0.25


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


def wilder_rsi(close: pd.Series, length: int) -> np.ndarray:
    delta = close.diff().to_numpy(dtype=float)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _wilder_rma(gain, length)
    avg_loss = _wilder_rma(loss, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
    rsi = np.where(avg_loss == 0, 100.0, rsi)
    rsi = np.where(np.isnan(avg_gain) | np.isnan(avg_loss), np.nan, rsi)
    return rsi


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int) -> np.ndarray:
    """Money Flow Index como ta.mfi de Pine: suma rodante simple (no Wilder)."""
    tp = (high + low + close) / 3.0
    raw_flow = tp * volume
    tp_prev = tp.shift(1)
    pos_flow = raw_flow.where(tp > tp_prev, 0.0)
    neg_flow = raw_flow.where(tp < tp_prev, 0.0)
    pos_sum = pos_flow.rolling(length).sum()
    neg_sum = neg_flow.rolling(length).sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        mr = pos_sum / neg_sum
        out = 100 - 100 / (1 + mr)
    out = out.where(neg_sum != 0, 100.0)
    return out.to_numpy(dtype=float)


def alpha_trend(df: pd.DataFrame, p: Params, volume_available: bool) -> np.ndarray:
    high, low, close = df["high"], df["low"], df["close"]
    atr = wilder_atr(high, low, close, p.at_period)
    up_t = (low - atr * p.at_mult).to_numpy()
    down_t = (high + atr * p.at_mult).to_numpy()

    use_volume = p.at_use_volume and volume_available
    momentum = mfi(high, low, close, df["volume"], p.at_period) if use_volume else wilder_rsi(close, p.at_period)

    n = len(df)
    at = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(up_t[i]) or np.isnan(down_t[i]) or np.isnan(momentum[i]):
            continue
        prev = at[i - 1] if i > 0 else np.nan
        if momentum[i] >= 50:
            at[i] = max(up_t[i], prev) if not np.isnan(prev) else up_t[i]
        else:
            at[i] = min(down_t[i], prev) if not np.isnan(prev) else down_t[i]
    return at


def pivots(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Réplica de ta.pivothigh/ta.pivotlow (ver nota en engine.py de upf_artillery)."""
    n = len(high)
    window = left + right + 1
    piv_h = np.full(n, np.nan)
    piv_l = np.full(n, np.nan)
    h_s, l_s = pd.Series(high), pd.Series(low)
    roll_max = h_s.rolling(window).max().to_numpy()
    roll_min = l_s.rolling(window).min().to_numpy()
    for i in range(window - 1, n):
        j = i - right
        if high[j] == roll_max[i]:
            piv_h[i] = high[j]
        if low[j] == roll_min[i]:
            piv_l[i] = low[j]
    return piv_h, piv_l


def pivot_point_supertrend(df: pd.DataFrame, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """
    Pivot Point SuperTrend (indicador público, el que el usuario identificó
    como "Pivot" en el gráfico -línea roja/verde escalonada). Fórmula
    estándar (script "Pivot Point Supertrend" de LonesomeTheBlue, muy
    replicado en la comunidad de TradingView):

      1. Pivotes de swing (ta.pivothigh/pivotlow, left=right=piv_period).
      2. Se promedian en una línea "center" con suavizado recursivo:
         center = pivote nuevo si es el primero, si no
         center = (center_anterior*2 + pivote_nuevo) / 3.
      3. Bandas: Up = center - Factor*ATR(piv_atr_period),
                 Dn = center + Factor*ATR(piv_atr_period).
      4. Trailing stop tipo SuperTrend sobre esas bandas: ratchetea
         mientras el cierre se mantenga del lado correspondiente, y el
         "trend" (soporte=1 / resistencia=-1) flipea cuando el cierre
         cruza la banda contraria.

    Devuelve (line, trend): `line` es el valor de la línea graficada
    (Tup cuando trend=1, Tdown cuando trend=-1); `trend` es 1/-1/NaN.
    """
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    atr = wilder_atr(df["high"], df["low"], df["close"], p.piv_atr_period)
    piv_h, piv_l = pivots(h, l, p.piv_period, p.piv_period)

    n = len(df)
    center = np.full(n, np.nan)
    last_center = np.nan
    for i in range(n):
        new_pp = piv_h[i] if not np.isnan(piv_h[i]) else (piv_l[i] if not np.isnan(piv_l[i]) else np.nan)
        if not np.isnan(new_pp):
            last_center = new_pp if np.isnan(last_center) else (last_center * 2 + new_pp) / 3.0
        center[i] = last_center

    up = center - p.piv_atr_factor * atr
    dn = center + p.piv_atr_factor * atr

    tup = np.full(n, np.nan)
    tdown = np.full(n, np.nan)
    trend = np.full(n, np.nan)
    line = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(up[i]) or np.isnan(dn[i]):
            continue
        prev_close = c[i - 1] if i > 0 else np.nan
        prev_tup = tup[i - 1] if i > 0 and not np.isnan(tup[i - 1]) else up[i]
        prev_tdown = tdown[i - 1] if i > 0 and not np.isnan(tdown[i - 1]) else dn[i]

        tup[i] = max(up[i], prev_tup) if (not np.isnan(prev_close) and prev_close > prev_tup) else up[i]
        tdown[i] = min(dn[i], prev_tdown) if (not np.isnan(prev_close) and prev_close < prev_tdown) else dn[i]

        prev_trend = trend[i - 1] if i > 0 and not np.isnan(trend[i - 1]) else 1.0
        if c[i] > prev_tdown:
            trend[i] = 1.0
        elif c[i] < prev_tup:
            trend[i] = -1.0
        else:
            trend[i] = prev_trend

        line[i] = tup[i] if trend[i] == 1.0 else tdown[i]

    return line, trend


def supply_demand_zones(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, atrpoi: np.ndarray, p: Params
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Zona de oferta/demanda del indicador "DIY [ZP]" (única parte de ese
    script que el usuario confirmó usar). Ver simplificación de "sólo la
    zona más reciente por lado" en el docstring del módulo.

    Devuelve (demand_top, demand_bottom, supply_top, supply_bottom,
    demand_id, supply_id). Los primeros 4 son arrays alineados a las
    velas con NaN cuando no hay zona activa de ese lado. `demand_id`/
    `supply_id` son enteros que identifican a CADA zona (se incrementan
    al nacer una nueva, 0 cuando no hay zona activa) -sirven para que
    `simulate()` detecte si una zona ya fue testeada antes (filtro de
    frescura, `Params.fresh_only`).
    """
    piv_h, piv_l = pivots(high, low, p.diy_swing_length, p.diy_swing_length)
    n = len(high)
    demand_top = np.full(n, np.nan)
    demand_bottom = np.full(n, np.nan)
    supply_top = np.full(n, np.nan)
    supply_bottom = np.full(n, np.nan)
    demand_id = np.zeros(n, dtype=int)
    supply_id = np.zeros(n, dtype=int)

    cur_demand = None  # (bottom, top, poi, id)
    cur_supply = None  # (bottom, top, poi, id)
    next_demand_id = 0
    next_supply_id = 0

    for i in range(n):
        atr_i = atrpoi[i]
        if not np.isnan(piv_h[i]) and not np.isnan(atr_i):
            buf = atr_i * (p.diy_box_width / 10.0)
            new_top, new_bottom = piv_h[i], piv_h[i] - buf
            new_poi = (new_top + new_bottom) / 2.0
            if cur_supply is None or abs(new_poi - cur_supply[2]) > atr_i * p.diy_overlap_atr_mult:
                next_supply_id += 1
                cur_supply = (new_bottom, new_top, new_poi, next_supply_id)
        if not np.isnan(piv_l[i]) and not np.isnan(atr_i):
            buf = atr_i * (p.diy_box_width / 10.0)
            new_bottom, new_top = piv_l[i], piv_l[i] + buf
            new_poi = (new_top + new_bottom) / 2.0
            if cur_demand is None or abs(new_poi - cur_demand[2]) > atr_i * p.diy_overlap_atr_mult:
                next_demand_id += 1
                cur_demand = (new_bottom, new_top, new_poi, next_demand_id)

        if cur_supply is not None and close[i] >= cur_supply[1]:
            cur_supply = None
        if cur_demand is not None and close[i] <= cur_demand[0]:
            cur_demand = None

        if cur_supply is not None:
            supply_bottom[i], supply_top[i] = cur_supply[0], cur_supply[1]
            supply_id[i] = cur_supply[3]
        if cur_demand is not None:
            demand_bottom[i], demand_top[i] = cur_demand[0], cur_demand[1]
            demand_id[i] = cur_demand[3]

    return demand_top, demand_bottom, supply_top, supply_bottom, demand_id, supply_id


def _within_session(ct_min: int, p: Params) -> tuple[bool, bool]:
    if not p.use_session:
        return True, False
    cutoff = p.session_close_hour * 60 + p.session_close_minute
    return ct_min < cutoff, ct_min >= cutoff


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    piv_line, _piv_trend = pivot_point_supertrend(df, p)

    atrpoi = wilder_atr(df["high"], df["low"], df["close"], p.diy_atr_len)
    demand_top, demand_bottom, supply_top, supply_bottom, demand_id, supply_id = supply_demand_zones(h, l, c, atrpoi, p)

    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)

    slip = p.slippage_ticks * p.tick_size

    last_day = None
    dtrades = 0

    open_pos = False
    is_long = False
    entry_price = entry_bar = None
    sl = tp = np.nan
    qty = 0
    entry_sources = ""

    # ── Estado del filtro de frescura (Params.fresh_only) ────────────
    at_side_prev = None
    at_tested_side = None
    piv_side_prev = None
    piv_tested_side = None
    supply_tested_id = 0
    demand_tested_id = 0

    trades = []

    for i in range(n):
        local = ts[i].tz_convert(p.session_tz)
        ct_min = local.hour * 60 + local.minute
        day_id = local.date()
        if day_id != last_day:
            dtrades = 0
            last_day = day_id
        in_sess, is_eod = _within_session(ct_min, p)

        # ── frescura: detectar flip de régimen SIEMPRE (incluso con
        #    posición abierta), para que el filtro no quede desalineado
        #    si el flip ocurrió mientras estábamos en una operación ──
        if p.fresh_only:
            at_side = None if np.isnan(alpha[i]) else (1 if c[i] > alpha[i] else -1)
            if at_side != at_side_prev:
                at_tested_side = None
            at_side_prev = at_side

            piv_side = None if np.isnan(piv_line[i]) else (1 if c[i] > piv_line[i] else -1)
            if piv_side != piv_side_prev:
                piv_tested_side = None
            piv_side_prev = piv_side

        # ── posición abierta: chequear SL/TRAIL/TP (SL primero si empatan) ─
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
                exit_price = c[i] - slip if is_long else c[i] + slip  # a mercado, cierre que flipea el trailing
                exit_reason = "TRAIL"
            elif hit_tp:
                exit_price = tp  # orden límite, sin slippage
                exit_reason = "TP"
            elif is_eod:
                exit_price = c[i] - slip if is_long else c[i] + slip
                exit_reason = "EOD"

            if exit_price is not None:
                pnl = (exit_price - entry_price) * qty * p.point_value_usd * (1 if is_long else -1)
                pnl -= p.commission_round_turn_usd * qty
                risk_pts = abs(entry_price - sl)
                trades.append({
                    "entry_time": ts[entry_bar],
                    "exit_time": ts[i],
                    "entry_bar": entry_bar,
                    "exit_bar": i,
                    "direction": "long" if is_long else "short",
                    "entry": entry_price,
                    "exit": exit_price,
                    "sl": sl,
                    "reason": exit_reason,
                    "qty": qty,
                    "pnl_usd": pnl,
                    "r_multiple": pnl / (risk_pts * qty * p.point_value_usd) if risk_pts and risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                    "sources": entry_sources,
                })
                open_pos = False

        # ── señales de entrada (sólo si está flat) ──────────────────────
        if not open_pos:
            in_entry_window = True
            if p.entry_start_hour is not None and p.entry_end_hour is not None:
                hour_frac = local.hour + local.minute / 60.0
                if p.entry_start_hour <= p.entry_end_hour:
                    in_entry_window = p.entry_start_hour <= hour_frac < p.entry_end_hour
                else:  # ventana que cruza medianoche (ej. sesión Asia 18-03)
                    in_entry_window = hour_frac >= p.entry_start_hour or hour_frac < p.entry_end_hour

            can_trade = dtrades < p.max_trades_per_day and in_sess and in_entry_window and not np.isnan(atr_risk[i])

            long_at = not np.isnan(alpha[i]) and c[i] > alpha[i] and l[i] < alpha[i]
            short_at = not np.isnan(alpha[i]) and c[i] < alpha[i] and h[i] > alpha[i]

            long_piv = not np.isnan(piv_line[i]) and c[i] > piv_line[i] and l[i] < piv_line[i]
            short_piv = not np.isnan(piv_line[i]) and c[i] < piv_line[i] and h[i] > piv_line[i]

            long_diy = not np.isnan(demand_top[i]) and l[i] < demand_top[i] and c[i] > demand_top[i]
            short_diy = not np.isnan(supply_bottom[i]) and h[i] > supply_bottom[i] and c[i] < supply_bottom[i]

            if p.fresh_only:
                if long_at or short_at:
                    if at_tested_side == at_side:
                        long_at = short_at = False
                    else:
                        at_tested_side = at_side
                if long_piv or short_piv:
                    if piv_tested_side == piv_side:
                        long_piv = short_piv = False
                    else:
                        piv_tested_side = piv_side
                if short_diy and supply_tested_id == supply_id[i]:
                    short_diy = False
                elif short_diy:
                    supply_tested_id = supply_id[i]
                if long_diy and demand_tested_id == demand_id[i]:
                    long_diy = False
                elif long_diy:
                    demand_tested_id = demand_id[i]

            if p.only_source == "at":
                long_piv = short_piv = long_diy = short_diy = False
            elif p.only_source == "piv":
                long_at = short_at = long_diy = short_diy = False
            elif p.only_source == "diy":
                long_at = short_at = long_piv = short_piv = False

            n_long = int(long_at) + int(long_piv) + int(long_diy)
            n_short = int(short_at) + int(short_piv) + int(short_diy)

            need = 1 if p.only_source else p.confluence_need
            long_go = can_trade and n_long >= need and n_short < need and p.only_direction != "short"
            short_go = can_trade and n_short >= need and n_long < need and p.only_direction != "long"

            if long_go or short_go:
                is_long = long_go
                entry_signal_price = c[i]
                entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
                entry_bar = i
                fired = ["at", "piv", "diy"]
                entry_sources = ",".join(
                    src for src, on in zip(fired, [long_at or short_at, long_piv or short_piv, long_diy or short_diy]) if on
                )
                if is_long:
                    sl = l[i] - atr_risk[i] * p.sl_buffer_atr
                    risk_pts = entry_price - sl
                    tp = entry_price + risk_pts * p.tp_r_mult
                else:
                    sl = h[i] + atr_risk[i] * p.sl_buffer_atr
                    risk_pts = sl - entry_price
                    tp = entry_price - risk_pts * p.tp_r_mult
                qty = min(p.max_qty, int(p.max_risk_usd / (risk_pts * p.point_value_usd))) if risk_pts > 0 else 0
                if qty > 0:
                    open_pos = True
                    dtrades += 1

    trades_df = pd.DataFrame(trades)
    summary = summarize(trades_df, volume_available)
    return trades_df, summary


def summarize(trades_df: pd.DataFrame, volume_available: bool) -> dict:
    base = {"volume_available": volume_available}
    if trades_df.empty:
        return {**base, "trades": 0, "win_rate": np.nan, "profit_factor": np.nan, "expectancy_r": np.nan,
                "net_pnl_usd": 0.0, "max_drawdown_usd": 0.0, "avg_bars_held": np.nan}
    wins = trades_df[trades_df["pnl_usd"] > 0]
    losses = trades_df[trades_df["pnl_usd"] <= 0]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = -losses["pnl_usd"].sum()
    equity = trades_df["pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    return {
        **base,
        "trades": len(trades_df),
        "win_rate": len(wins) / len(trades_df),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "expectancy_r": trades_df["r_multiple"].mean(),
        "net_pnl_usd": trades_df["pnl_usd"].sum(),
        "max_drawdown_usd": drawdown.min(),
        "avg_bars_held": trades_df["bars_held"].mean(),
    }
