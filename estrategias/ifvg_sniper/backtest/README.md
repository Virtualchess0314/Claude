# Backtester de IFVG Sniper

Réplica en Python de la lógica exacta de `ifvg_sniper.pine`, para barrer
parámetros mucho más rápido que probando manualmente en TradingView.

## Comparación entre timeframes (3m / 5m / 10m)

Cada timeframe tiene SU PROPIA config óptima — los filtros no son
transferibles entre timeframes (ver detalle en `runs/2026-09-05_mnq*m_*.txt`).
Con comisión real ($3.50 RT) y maxRiskUSD=300 en los tres casos:

| Timeframe | Período | Trades | PF | Winrate | Expectancy | Drawdown | Trades/día |
|---|---|---|---|---|---|---|---|
| 3m | 1 mes | 348 | 1.38 | 62% | 0.15R | -$2.015 | ~10.5 |
| 5m | 2 meses | 95 | **2.09** | 60% | **0.42R** | -$1.623 | ~1.8 |
| 10m | 2 meses | 84 | **2.12** | **69%** | 0.34R | **-$960** | ~1.6 |

**3m sale claramente peor una vez metidos los costos reales**: PF mucho más
bajo, más drawdown, y ~6x más operaciones por día — justo lo opuesto a la
intención original de evitar comportamiento de alta frecuencia. 5m y 10m
quedan parejos en calidad (10m gana en winrate/drawdown, 5m en expectancy
y plata neta por ser más activo). El `.pine` sigue con la config de 5m.

## Por qué esto no trae datos incluidos

Este backtester corre en un entorno sin salida a internet hacia proveedores
de datos de mercado (Yahoo Finance, Binance, etc. están bloqueados por
política de red). No hay forma de descargar velas de MNQ desde acá.

**Vos tenés que exportar el historial vos mismo desde TradingView:**

1. Abrí el gráfico de MNQ (o NQ) en el timeframe que quieras probar (1m, 3m...).
2. Cargá todo el historial posible (scrolleá hacia atrás para que TradingView
   lo vaya cargando, según tu plan).
3. Ícono de cámara/exportar en la barra de herramientas del gráfico →
   **"Export chart data"** → descargá el CSV.
4. Subí ese CSV a este repo (carpeta `estrategias/ifvg_sniper/backtest/data/`,
   por ejemplo) o pegámelo/mandámelo para que lo suba yo.

El loader (`data.py`) acepta el formato tal cual lo exporta TradingView
(columna `time` en unix timestamp + `open,high,low,close`).

## Uso

```bash
pip install -r requirements.txt

# Barrido con los rangos por defecto (min_gap_atr, sl_atr_mult, rr_target)
python3 optimize.py ruta/a/tus_datos.csv

# Barrido custom
python3 optimize.py ruta/a/tus_datos.csv \
    --min-gap-atr 0.5,0.75,1.0 \
    --sl-atr-mult 0.5,1.0,1.5 \
    --rr-target 0.5,1.0,1.5,2.0 \
    --min-trades 30
```

Esto reparte el historial en **70% train / 30% test** (en el tiempo, nunca
mezclado al azar) y ordena el ranking por el resultado en **test**, no en
train — una combinación que sólo funciona en el pasado que ya "vio" no sirve
para operar mañana. Los resultados completos quedan en `optimize_results.csv`.

## Qué mirar en el resultado, en orden de importancia

1. **`test_trades`** — si hay pocas operaciones en test, cualquier métrica de
   esa fila es ruido. No confíes en una combinación con 5 trades aunque el
   profit factor parezca espectacular.
2. **`test_profit_factor` y `train_profit_factor` parecidas** — si train da
   PF 3.0 y test da PF 0.8, esa combinación está sobreajustada al pasado.
   Buscá combinaciones donde ambos números sean razonablemente consistentes.
3. **`test_max_drawdown_usd`** — cuánto capital necesitás aguantar en la peor
   racha, en dólares reales según tu `point_value_usd` y `max_risk_usd`.
4. **`test_avg_bars_held`** — para chequear que seguís lejos del límite de
   "alta frecuencia" de tu cuenta fondeada (ver comentario en el .pine).

## Corridas guardadas

`runs/` guarda snapshots de resultados de barridos ya hechos (a diferencia
del CSV de precios, que no se versiona por ser dato de mercado con licencia
de TradingView). La corrida vigente en los inputs del `.pine`:

- `runs/2026-09-05_mnq5m_rr1.5-2.0_risk300.csv` — MNQ 5m, 11.040 velas
  (12 jul – 4 sep 2026), train 70% / test 30%. Combinación elegida:
  `minGapAtr=0.75, slAtrMult=0.75, rrTarget=1.5` — PF 1.57 train / 1.49 test,
  ~50% winrate en ambos (la más consistente del barrido, no la de mayor PF
  a secas). **Todavía sin comisión ni slippage.**

## Desglose por horario/sesión/día (`analyze_sessions.py`)

```bash
python3 analyze_sessions.py tus_datos.csv \
    --min-gap-atr 0.75 --sl-atr-mult 0.75 --rr-target 1.5 --max-risk-usd 300
```

Corre la config elegida y parte las operaciones resultantes por hora del
día, ventana de sesión (Asia/Londres/NY), día de la semana y dirección
(long/short), para ver DÓNDE está el winrate. Es un análisis descriptivo,
no una optimización más — cortar el mismo puñado de operaciones en cada vez
más categorías es la forma más fácil de "encontrar" un patrón que en
realidad es ruido, así que cada tabla imprime el número de operaciones de
cada bucket: con pocos trades, no es señal.

**Hallazgo validado con MNQ 5m (jul-sep 2026):** restringir las entradas a
la sesión de Nueva York (08:00–16:00 ET) sube el PF de forma consistente en
train Y test (1.57→1.84 train, 1.49→2.52 test; winrate ~50%→~55-60%), a
costa de ~40% menos operaciones. A diferencia del corte por día de la
semana (donde 32 trades de "lunes" salen de sólo ~8-9 lunes reales — muestra
insuficiente para confiar), esto se validó re-corriendo train/test por
separado, no mirando una sola tabla. Implementado como filtro opcional
(`useEntryWindow`) en el `.pine`, activado por defecto.

## Filtros de calidad de señal (edad del IFVG, forma de vela, buffers)

Con la ventana NY ya fijada, se barrieron `max_ifvg_age`, `min_body_ratio`,
`min_range_atr`, `clean_break_buffer_atr` y `touch_tol_atr` (405
combinaciones, ver `runs/2026-09-05_mnq5m_calidad-senal_buffer0.10.csv`).

- **`clean_break_buffer_atr` 0.05→0.10 fue la única mejora limpia**: exigir
  una ruptura más decisiva (para confirmar tanto el flip como la
  invalidación) subió el PF de forma consistente en train (1.84→2.34) y
  test (2.52→2.70). Aplicado como nuevo default en el `.pine`.
- `max_ifvg_age`, `min_range_atr` y `touch_tol_atr`: sin cambios respecto a
  los valores por defecto — el barrido confirma que ya estaban bien
  (ninguna alternativa los superó de forma consistente).
- `min_body_ratio` 0.3 (más laxo) daba un PF de train todavía más alto
  (2.72) pero sin mejorar el test respecto al 0.10 solo en buffer — se
  descartó por agregar un segundo cambio sin beneficio claro adicional.

**Filtro de tendencia (EMA) — probado y descartado:** `engine.py` soporta
`trend_ema_len` (sólo tomar longs con cierre > EMA, shorts con cierre < EMA)
pero empeoró los resultados con EMA 20/50/100/200 en todos los casos. Tiene
sentido: esta estrategia es de reversión (opera el rechazo en una zona ya
invertida), así que forzar alineación con una tendencia mayor filtra
justamente las mejores señales de reversión. No se expone en el `.pine`.

## Comisión y slippage (`cost_sensitivity.py`)

```bash
python3 cost_sensitivity.py tus_datos.csv
```

Este entorno no tiene salida a internet para confirmar la tarifa vigente de
Tradeify/Tradovate/CME, así que en vez de asumir un número exacto (y quizás
equivocado), el script corre la config actual con varios escenarios de
comisión "round-turn" por contrato (bróker + CME + NFA todo junto) y de
slippage, en train y test por separado. Ver
`runs/2026-09-05_mnq5m_cost_sensitivity.txt` para la corrida completa.

**Comisión real confirmada por el usuario: $3.50 round-turn por contrato**
(Tradovate/Tradeify, comisión + CME + NFA todo incluido). Con eso, y 1 tick
de slippage en salidas de mercado (SL / cierre de sesión):

| | Trades | PF | Winrate | Expectancy | Neto | Drawdown |
|---|---|---|---|---|---|---|
| Train | 63 | 2.08 | 60.3% | 0.44R | $7.553 | -$1.623 |
| Test | 31 | 2.29 | 61.3% | 0.44R | $4.330 | -$1.205 |
| **Completo** | **95** | **2.09** | **60.0%** | **0.42R** | **$11.590** | -$1.623 |

El punto de equilibrio (profit factor = 1.0) está en **~$23 de comisión
round-turn por contrato** — con la comisión real de $3.50 hay **~6-7x de
margen** antes de que el costo se coma el edge. El tamaño de posición
promedio de esta config es ~5-7 contratos (con `maxRiskUSD=300` y SL de
0.75×ATR sobre un ATR mediano de ~26 puntos → ~$40 de riesgo/contrato →
300/40 ≈ 7), lo que a $3.50/contrato es ~$20-25 de comisión por operación.

**Límite de contratos confirmado: 40 micros** (cuenta de 50k de Tradeify).
`max_qty` en `engine.py` y `maxQty` en el `.pine` se actualizaron de 20
(tope arbitrario puesto sin saber el límite real) a 40. Con este límite
real, ninguna operación llega a tocarlo ni con `maxRiskUSD=300` ni con 500
(máximo visto: 22 contratos) — hay margen cómodo.

## R:R 1:1 vs 1:1.5, y maxRiskUSD 300 vs 500

Con comisión real ($3.50 RT) y `maxQty=40`:

| maxRiskUSD | R:R | Trades | PF | Winrate | Expectancy | Neto | Drawdown | Duración |
|---|---|---|---|---|---|---|---|---|
| 300 | 1:1 | 97 | 2.09 | **70.1%** | 0.34R | $9.145 | -$1.305 | ~3 min |
| 300 | 1:1.5 (vigente en el `.pine`) | 95 | 2.09 | 60.0% | 0.42R | $11.590 | -$1.623 | ~8.5 min |
| 500 | 1:1 | 97 | 2.08 | **70.1%** | 0.34R | $15.575 | -$2.246 | ~3 min |
| 500 | 1:1.5 | 95 | 2.04 | 60.0% | 0.42R | $19.288 | -$2.839 | ~8.5 min |

Trade-offs a decidir según prioridad, no hay un lado objetivamente mejor:
- **1:1** da más winrate y menor drawdown, pero menos plata neta y
  duración de trade mucho más corta (~3 min) — más cerca del límite de
  "alta frecuencia" que penalizan las cuentas fondeadas.
- **1:1.5** da más expectancy y más plata neta, con trades más espaciados
  (~8.5 min), a costa de menor winrate y mayor drawdown.
- Subir `maxRiskUSD` de 300 a 500 escala el resultado casi proporcionalmente
  (más plata, más drawdown en dólares) sin cambiar PF/winrate — es
  apalancamiento, no una mejora real de la estrategia. Confirmá que tu
  cuenta tolera un drawdown de ~$2.800 (peor caso visto, 1:1.5 a $500)
  dentro de sus reglas de pérdida máxima antes de subir el riesgo.

## Limitaciones a tener en cuenta

- Si en la misma vela se tocan SL y TP, el motor asume que el SL se ejecutó
  primero (supuesto conservador). El Strategy Tester de TradingView tiene la
  misma ambigüedad salvo que actives "bar magnifier".
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
- Una vez que tengas una combinación ganadora acá, hay que llevarla de vuelta
  a los inputs del `.pine` y confirmarla corriendo el Strategy Tester (o
  Deep Backtesting) de TradingView sobre esos mismos datos, como control
  cruzado antes de operarla en real.
