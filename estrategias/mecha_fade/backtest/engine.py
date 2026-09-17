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
el código del indicador "DIY Custom Strategy Builder [ZP]"):

  - AlphaTrend: indicador externo, no está en el script "DIY". Fórmula
    pública estándar (ratchet sobre ATR/RSI o ATR/MFI).
  - Pivot: Pivot Points clásicos de piso ("Traditional"), anclados al día
    de trading anterior (P, R1-R3, S1-S3). Es un indicador aparte del
    "DIY" (ese script trae su propia sección de Pivot Points, pero el
    usuario aclaró que del "DIY" sólo usa la parte de soportes/resistencias
    -ver abajo- así que Pivot se trata como una fuente independiente con
    la fórmula clásica de piso).
  - DIY: el usuario confirmó que de ese script sólo usa la función de
    "Supply/Demand Zone" (las cajas soporte/resistencia ancladas a los
    últimos swing high/low, con un buffer de ATR(50) y lógica de ruptura/
    BOS) -no el motor de señales de ~35 indicadores líder + confirmaciones
    que trae el resto del script. Replicado en `supply_demand_zones()`.

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

    # Pivot (floor pivots clásicos, "Traditional", ancla diaria)
    session_tz: str = "America/New_York"

    # DIY = Supply/Demand Zone del indicador ZP (ver docstring del módulo)
    diy_swing_length: int = 10
    diy_atr_len: int = 50
    diy_box_width: float = 2.5       # buffer de la zona = ATR(diy_atr_len) * box_width/10
    diy_overlap_atr_mult: float = 2.0  # no reemplaza la zona activa si el nuevo swing cae más cerca que esto

    # Confluencia: cuántos de los 3 indicadores deben coincidir (1..3)
    confluence_need: int = 1

    # Riesgo / salida
    atr_len: int = 14
    sl_buffer_atr: float = 0.10
    tp_r_mult: float = 1.5
    max_risk_usd: float = 150.0
    point_value_usd: float = 2.0
    max_qty: int = 40

    # Sesión / control diario
    use_session: bool = True
    session_close_hour: int = 16
    session_close_minute: int = 45
    max_trades_per_day: int = 999  # 999 = sin límite práctico

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


def classic_daily_pivots(df: pd.DataFrame, session_tz: str) -> pd.DataFrame:
    """
    Floor pivots "Traditional" (P, R1-R3, S1-S3), calculados con el
    High/Low/Close del día de trading ANTERIOR (ancla diaria, igual al
    default "Auto"/"Daily" + "Use Daily-based Values" de ta.pivot_point_levels
    en Pine). El día de trading se define por fecha calendario en
    `session_tz` -aproximación razonable ya que sólo tenemos velas
    intradía, no un chart diario separado (documentado, no escondido).
    Devuelve un DataFrame alineado 1:1 con `df` (mismo índice), con
    columnas pp/r1/s1/r2/s2/r3/s3. Las velas del primer día quedan en NaN
    (no hay día anterior del cual calcular).
    """
    local_dates = df.index.tz_convert(session_tz).date
    daily = pd.DataFrame({
        "date": local_dates,
        "high": df["high"].to_numpy(),
        "low": df["low"].to_numpy(),
        "close": df["close"].to_numpy(),
    })
    daily_ohlc = daily.groupby("date").agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    prev = daily_ohlc.shift(1)

    pp = (prev["h"] + prev["l"] + prev["c"]) / 3.0
    r1 = 2 * pp - prev["l"]
    s1 = 2 * pp - prev["h"]
    r2 = pp + (prev["h"] - prev["l"])
    s2 = pp - (prev["h"] - prev["l"])
    r3 = prev["h"] + 2 * (pp - prev["l"])
    s3 = prev["l"] - 2 * (prev["h"] - pp)

    levels = pd.DataFrame({"pp": pp, "r1": r1, "s1": s1, "r2": r2, "s2": s2, "r3": r3, "s3": s3})
    return levels.reindex(local_dates).reset_index(drop=True).set_index(df.index)


def supply_demand_zones(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, atrpoi: np.ndarray, p: Params
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Zona de oferta/demanda del indicador "DIY [ZP]" (única parte de ese
    script que el usuario confirmó usar). Ver simplificación de "sólo la
    zona más reciente por lado" en el docstring del módulo.

    Devuelve (demand_top, demand_bottom, supply_top, supply_bottom), cada
    uno un array alineado a las velas con NaN cuando no hay zona activa
    de ese lado.
    """
    piv_h, piv_l = pivots(high, low, p.diy_swing_length, p.diy_swing_length)
    n = len(high)
    demand_top = np.full(n, np.nan)
    demand_bottom = np.full(n, np.nan)
    supply_top = np.full(n, np.nan)
    supply_bottom = np.full(n, np.nan)

    cur_demand = None  # (bottom, top, poi)
    cur_supply = None  # (bottom, top, poi)

    for i in range(n):
        atr_i = atrpoi[i]
        if not np.isnan(piv_h[i]) and not np.isnan(atr_i):
            buf = atr_i * (p.diy_box_width / 10.0)
            new_top, new_bottom = piv_h[i], piv_h[i] - buf
            new_poi = (new_top + new_bottom) / 2.0
            if cur_supply is None or abs(new_poi - cur_supply[2]) > atr_i * p.diy_overlap_atr_mult:
                cur_supply = (new_bottom, new_top, new_poi)
        if not np.isnan(piv_l[i]) and not np.isnan(atr_i):
            buf = atr_i * (p.diy_box_width / 10.0)
            new_bottom, new_top = piv_l[i], piv_l[i] + buf
            new_poi = (new_top + new_bottom) / 2.0
            if cur_demand is None or abs(new_poi - cur_demand[2]) > atr_i * p.diy_overlap_atr_mult:
                cur_demand = (new_bottom, new_top, new_poi)

        if cur_supply is not None and close[i] >= cur_supply[1]:
            cur_supply = None
        if cur_demand is not None and close[i] <= cur_demand[0]:
            cur_demand = None

        if cur_supply is not None:
            supply_bottom[i], supply_top[i] = cur_supply[0], cur_supply[1]
        if cur_demand is not None:
            demand_bottom[i], demand_top[i] = cur_demand[0], cur_demand[1]

    return demand_top, demand_bottom, supply_top, supply_bottom


def _within_session(ct_min: int, p: Params) -> tuple[bool, bool]:
    if not p.use_session:
        return True, False
    cutoff = p.session_close_hour * 60 + p.session_close_minute
    return ct_min < cutoff, ct_min >= cutoff


_PIVOT_COLS = ("pp", "r1", "s1", "r2", "s2", "r3", "s3")


def simulate(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ts = df.index
    n = len(df)
    volume_available = "volume" in df.columns

    alpha = alpha_trend(df, p, volume_available)
    pivot_levels = classic_daily_pivots(df, p.session_tz).to_numpy()  # shape (n, 7), orden = _PIVOT_COLS

    atrpoi = wilder_atr(df["high"], df["low"], df["close"], p.diy_atr_len)
    demand_top, demand_bottom, supply_top, supply_bottom = supply_demand_zones(h, l, c, atrpoi, p)

    atr_risk = wilder_atr(df["high"], df["low"], df["close"], p.atr_len)

    slip = p.slippage_ticks * p.tick_size

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

        # ── posición abierta: chequear SL/TP (SL primero si empatan) ────
        if open_pos:
            hit_sl = (l[i] <= sl) if is_long else (h[i] >= sl)
            hit_tp = (h[i] >= tp) if is_long else (l[i] <= tp)
            exit_price = exit_reason = None
            if hit_sl:
                exit_price = sl - slip if is_long else sl + slip
                exit_reason = "SL"
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
                    "direction": "long" if is_long else "short",
                    "entry": entry_price,
                    "exit": exit_price,
                    "reason": exit_reason,
                    "qty": qty,
                    "pnl_usd": pnl,
                    "r_multiple": pnl / (risk_pts * qty * p.point_value_usd) if risk_pts and risk_pts > 0 else np.nan,
                    "bars_held": i - entry_bar,
                })
                open_pos = False

        # ── señales de entrada (sólo si está flat) ──────────────────────
        if not open_pos:
            can_trade = dtrades < p.max_trades_per_day and in_sess and not np.isnan(atr_risk[i])

            long_at = not np.isnan(alpha[i]) and c[i] > alpha[i] and l[i] < alpha[i]
            short_at = not np.isnan(alpha[i]) and c[i] < alpha[i] and h[i] > alpha[i]

            long_piv = False
            short_piv = False
            for k in range(len(_PIVOT_COLS)):
                lvl = pivot_levels[i, k]
                if np.isnan(lvl):
                    continue
                if l[i] < lvl and c[i] > lvl:
                    long_piv = True
                if h[i] > lvl and c[i] < lvl:
                    short_piv = True

            long_diy = not np.isnan(demand_top[i]) and l[i] < demand_top[i] and c[i] > demand_top[i]
            short_diy = not np.isnan(supply_bottom[i]) and h[i] > supply_bottom[i] and c[i] < supply_bottom[i]

            n_long = int(long_at) + int(long_piv) + int(long_diy)
            n_short = int(short_at) + int(short_piv) + int(short_diy)

            long_go = can_trade and n_long >= p.confluence_need and n_short < p.confluence_need
            short_go = can_trade and n_short >= p.confluence_need and n_long < p.confluence_need

            if long_go or short_go:
                is_long = long_go
                entry_signal_price = c[i]
                entry_price = entry_signal_price + slip if is_long else entry_signal_price - slip
                entry_bar = i
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
