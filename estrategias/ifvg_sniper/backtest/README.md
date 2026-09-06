# Backtester de IFVG Sniper

Réplica en Python de la lógica exacta de `ifvg_sniper.pine`, para barrer
parámetros mucho más rápido que probando manualmente en TradingView.

## Comparación entre timeframes (3m / 5m / 10m / 15m / 30m)

Cada timeframe tiene SU PROPIA config óptima — los filtros no son
transferibles entre timeframes (ver detalle en `runs/2026-09-05_mnq*m_*.txt`).
Con comisión real ($3.50 RT) y maxRiskUSD=300:

| Timeframe | Período | Trades | PF | Winrate | Expectancy | Drawdown | Trades/día |
|---|---|---|---|---|---|---|---|
| 3m | 1 mes | 348 | 1.38 | 62% | 0.15R | -$2.015 | ~10.5 |
| 5m | 2 meses | 95 | **2.09** | 60% | **0.42R** | -$1.623 | ~1.8 |
| 10m | 2 meses | 84 | **2.12** | **69%** | 0.34R | **-$960** | ~1.6 |
| 15m | 2 meses | — | **sin edge** | — | — | — | — |
| 30m | 2 meses | — | **sin edge** | — | — | — | — |

**3m sale claramente peor una vez metidos los costos reales**: PF mucho más
bajo, más drawdown, y ~6x más operaciones por día — justo lo opuesto a la
intención original de evitar comportamiento de alta frecuencia. 5m y 10m
quedan parejos en calidad (10m gana en winrate/drawdown, 5m en expectancy
y plata neta por ser más activo). El `.pine` sigue con la config de 5m.

**15m y 30m: no se encontró ninguna combinación con edge consistente
train/test** en el barrido de 48 combinaciones para cada uno — hasta la
"mejor" por consistencia da PF de test por debajo de 1.0, y varias
combinaciones muestran sobreajuste clásico (PF de train altísimo con una
muestra chica, PF de test muy por debajo). No es un bug del motor (85 y 33
trades respectivamente sobre el dataset completo con parámetros default,
apenas por encima de breakeven sin filtrar nada) — el patrón simplemente
ocurre con mucha menos frecuencia en velas grandes, y 2 meses no alcanzan
para juntar una muestra de operaciones que se pueda partir en train/test
con confianza. Ver `runs/2026-09-05_mnq15m-30m_sin-edge.txt`. No se
recomienda operar estos dos timeframes con la evidencia actual — haría
falta bastante más historial para volver a intentarlo.

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

**Filtro de BIAS por timeframe mayor (HTF) — probado y descartado:**
variante de la idea anterior pero calculando el bias en 1h (derivado por
resample del propio CSV) en vez del mismo timeframe — `engine.py` soporta
`htf_bias_ema_len`/`htf_bias_rule` (ver `compute_htf_bias()`). Resultado
con las configs vigentes de cada timeframe (EMA de 1h en 20/50/100/200,
split train/test 70/30, comisión+slippage reales):

| Timeframe | Sin filtro (PF train/test) | Mejor con bias (PF train/test) |
|---|---|---|
| 1m | 1.50 / **2.30** | 2.68-4.06 / 0.93-0.94 (peor en TODOS los `ema_len`) |
| 3m | 1.30 / **1.58** | 1.00-1.28 / 0.91-1.09 (peor en TODOS los `ema_len`, incluso en TRAIN) |
| 5m | 2.08 / **2.29** | 2.23-4.96 / 0.72-1.31 (peor en TODOS los `ema_len`) |
| 15m | 1.46 / 1.35 | 1.48 / **1.51** (con EMA 20; mejora chica, ~40% menos operaciones) |

En 1m y 5m el patrón es el mismo que con la EMA del mismo timeframe: el
filtro **mejora train y arruina test** de forma consistente en las cuatro
longitudes de EMA probadas — señal clara de que el filtro está sacando
justo las operaciones de reversión que funcionaban, no ruido. En 3m el
filtro ni siquiera mejora train (PF cae a ~1.00-1.28 vs 1.30 sin filtro) —
la peor de las cuatro corridas. En 15m la mejora con EMA 20 es real pero
chica y con bastante menos muestra (73/38 operaciones train/test vs 90/44
sin filtro) como para confirmarla con este único split. **Conclusión: no
usar bias por HTF tampoco** — confirmado en 4 de 4 timeframes probados
(1m/3m/5m/15m), refuerza que esta estrategia funciona mejor operando la
reversión sin filtro direccional. No se expone en el `.pine`.

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

## maxRiskUSD 300 vs 600, en los 4 timeframes

Mismo test de arriba (R:R vigente / 1:0.75 / 1:0.5) repetido con
`max_risk_usd=600` en vez de 300, para ver si escala limpio o empieza a
toparse con `maxQty=40`:

| Timeframe | R:R | PF test (300→600) | Neto test (300→600) | Drawdown test (300→600) | Qty prom. | Trades topados a qty=40 (train/test) |
|---|---|---|---|---|---|---|
| 1m | 1:1 | 2.30→2.16 | $2.174→$3.925 | -$694→-$1.413 | 30.4 | **2/39 · 7/22 (32% del test)** |
| 3m | 1:1 | 1.58→1.59 | $6.707→$14.070 | -$1.276→-$2.679 | 16.9 | 1/230 · 1/112 (negligible) |
| 5m | 1:1.5 | 2.29→2.24 | $4.330→$8.678 | -$1.205→-$2.513 | 14.5 | 0 · 0 |
| 15m | 1:1.5 | 1.35→1.37 | $2.070→$4.609 | -$1.943→-$4.252 | 8.3 | 0 · 0 |

PF y winrate se mantienen casi iguales en los 4 (subir el riesgo no
cambia el edge, sólo el tamaño de posición) — **excepto en 1m**, donde a
$600 el tamaño de posición promedio (30.4 contratos) queda tan cerca del
tope de 40 que **el 32% de las operaciones de test topan el límite**: en
esos trades el riesgo real termina siendo MENOR al nominal de $600 (el
motor no puede pedir más de 40 contratos), así que el neto de 1m a $600
NO es el doble limpio de $300 — está parcialmente frenado por el tope de
la cuenta. En 3m/5m/15m el tope no se toca prácticamente nunca, así que
ahí sí escala limpio.

**Drawdown**: el peor caso visto es 15m a 1:1.5/$600 (-$4.252) — más del
doble que a $300. Si vas a subir a $600, confirmá contra el límite de
pérdida máxima/diaria de tu cuenta fondeada específica antes de asumirlo,
sobre todo en 15m con el R:R vigente (1.5); a 1:0.75 el drawdown de 15m a
$600 es bastante menor (-$1.808), coherente con el hallazgo de la sección
anterior de que 1:0.75 es más robusto en ese timeframe.

## R:R más bajo (1:0.75 y 1:0.5) para cuentas de fondeo

Hipótesis a probar: un R:R más chico (TP más cerca que el SL) sube el
winrate y acorta las rachas de operaciones perdedoras seguidas — algo que
puede convenir en una cuenta fondeada con límite de pérdida diaria/máxima,
aunque baje la plata neta. Se probó `rr_target=0.75` y `0.5` en los 4
timeframes con datos reales (manteniendo el resto de la config vigente de
cada uno), split train/test 70/30, comisión+slippage reales:

`ATR(14)` es el promedio del período de test de cada timeframe (varía
vela a vela; esto es sólo la referencia típica). SL = `sl_atr_mult` ×
ATR (fijo por timeframe, no cambia con el R:R); TP = R:R × SL. En dólares,
multiplicar por `point_value_usd=2` (MNQ) — ej. 1m: SL 9.6 pts ≈ $19.

| Timeframe | R:R | ATR(14) prom. (test) | SL (pts) | TP (pts) | Winrate train/test | PF train/test | Neto test | Drawdown test | Racha SL train/test |
|---|---|---|---|---|---|---|---|---|---|
| 1m | 1:1 (vigente) | 9.6 | 9.6 | 9.6 | 69%/77% | 1.50/**2.30** | $2.174 | -$694 | 3/2 |
| 1m | 1:0.75 | 9.6 | 9.6 | 7.2 | 77%/82% | 1.54/2.12 | $1.472 | -$651 | 2/2 |
| 1m | 1:0.5 | 9.6 | 9.6 | 4.8 | 85%/86% | 1.46/1.61 | $596 | -$651 | 2/2 |
| 3m | 1:1 (vigente) | 18.2 | 18.2 | 18.2 | 61%/66% | 1.30/**1.58** | $6.707 | -$1.276 | 6/3 |
| 3m | 1:0.75 | 18.2 | 18.2 | 13.6 | 69%/72% | 1.35/1.49 | $4.718 | -$903 | 5/3 |
| 3m | 1:0.5 | 18.2 | 18.2 | 9.1 | 79%/77% | 1.48/1.22 | $1.741 | -$1.334 | 4/2 |
| 5m | 1:1.5 (vigente) | 24.1 | 18.1 | 27.2 | 60%/61% | 2.08/**2.29** | $4.330 | -$1.205 | 6/4 |
| 5m | 1:0.75 | 24.1 | 18.1 | 13.6 | 73%/72% | 1.91/1.60 | $1.644 | -$1.205 | 2/4 |
| 5m | 1:0.5 | 24.1 | 18.1 | 9.1 | 81%/78% | 2.07/1.34 | $745 | -$925 | 2/3 |
| 15m | 1:1.5 (vigente) | 49.2 | 36.9 | 55.4 | 52%/50% | 1.46/1.35 | $2.070 | -$1.943 | 7/5 |
| 15m | **1:0.75** | 49.2 | 36.9 | **27.7** | **69%/73%** | **1.55/1.82** | **$2.615** | **-$901** | **6/3** |
| 15m | 1:0.5 | 49.2 | 36.9 | 18.5 | 75%/75% | 1.48/1.34 | $979 | -$1.496 | 3/3 |

**En 1m, 3m y 5m el patrón confirma la hipótesis sólo a medias:** sube el
winrate y achica algo la racha de SL, pero el profit factor y sobre todo
la plata neta de test caen bastante — en 3m y 5m, a 1:0.5 el PF de test
queda apenas por encima de 1 (1.22 y 1.34) con muy poco neto. Es un
trade-off real, no una mejora gratis: 1:0.75 es un punto medio razonable
si lo que más importa es suavizar la curva, pero 1:0.5 cede demasiado
edge para lo poco que reduce el riesgo de racha (que ya era bajo en 1m/5m
con el R:R vigente).

**En 15m, 1:0.75 es una mejora real, no un trade-off** — mejor PF en
train Y test (1.35→1.82 en test), más neto en test, drawdown mucho menor
(-$1.943→-$901) y racha de SL más corta. Con 90-93 operaciones en train y
44 en test, tiene mejor base de muestra que 1m para confiar en el
resultado. Candidato serio para reemplazar `rr_target=1.5` en 15m si se
llega a operar ese timeframe — pendiente de confirmar con más historial y
en el Strategy Tester de TradingView antes de llevarlo al `.pine`.

## Cuándo se manda la señal, y cuánto tiempo hay hasta el fill real

**Dónde se manda:** en el `.pine`, la señal se manda por la función
`alert()` (`ifvg_sniper.pine` líneas ~333-347) — es un mecanismo
DISTINTO de la etiqueta visual (`label.new`, líneas ~278-301), aunque las
dos disparan en el mismo instante porque las controla el mismo par de
flags (`orderPlacedLong`/`orderPlacedShort`, fijados cuando se encuentra
una zona elegible, líneas ~197-210). O sea: la etiqueta que ves en el
gráfico y la alerta que te llega al celular/mail se generan juntas, no una
antes que la otra. Para que TradingView dispare esta alerta (y no la
genérica de "Order fills", que llega recién cuando ya se llenó) hay que
crearla eligiendo **"Any alert() function call"** como condición.

**Cuándo exactamente, dentro de la vela:** la estrategia tiene
`calc_on_every_tick = false` (`ifvg_sniper.pine` línea 49), así que TODO
el script —incluida la detección de zona, el cálculo de `orderPlacedLong`/
`Short` y el `alert()`— se recalcula UNA sola vez por vela, al **cierre**
de la vela confirmada. No hay parpadeo intrabar ni repintado: la señal no
puede aparecer y desaparecer dentro de la misma vela en formación, sale
una vez, al cierre, y ya.

**Cuánto tiempo real pasa hasta que se llena** (medido en el backtest:
velas entre que se decide la señal y que el precio efectivamente toca el
nivel límite, config vigente de cada timeframe):

| Timeframe | Mediana | p75 | p90 | % llenadas en la vela siguiente | % llenadas en ≤10 velas |
|---|---|---|---|---|---|
| 1m | 1 vela (1 min) | 4 velas | 15 velas | 62% | 84% |
| 3m | 1 vela (3 min) | 3 velas | 9 velas | 63% | 91% |
| 5m | 1 vela (5 min) | 6 velas | 15 velas | 58% | 82% |
| 15m | 1 vela (15 min) | 4 velas | 13 velas | 61% | 86% |

En **~60% de las operaciones, el fill ocurre en la vela inmediatamente
siguiente a la señal** — ese es el caso más ajustado: tenés sólo el
equivalente a 1 vela completa (1/3/5/15 min según el timeframe) para
cargar la orden límite en el bróker antes de que el precio la toque. El
otro ~40% da bastante más margen (mediana del `p75` en 3-6 velas), y hay
una cola larga de operaciones que tardan mucho más en tocarse (hasta
40-57 velas, cerca del límite de `max_ifvg_age=60`) — en esos casos hay
de sobra. Como la entrada es con orden LÍMITE (no de mercado), el riesgo
real de "que se te vaya el precio" no es slippage — es no llegar a cargar
la orden dentro de esa primera vela en el ~60% de los casos más ajustados;
una vez cargada, se llena sola sin que tengas que reaccionar de nuevo.

## Ventana horaria ajustada a disponibilidad real (Madrid 9:30-22:00)

Objetivo: operar 1-2 veces por día, disponible de 9:30 a 22:00 hora de
Madrid (con DST activo en ambos lados en sep-2026, Madrid = NY + 6h →
equivale a **NY 03:30-16:00**).

- **5m**: la ventana vigente (NY 08:00-16:00 = Madrid 14:00-22:00) ya
  queda completamente adentro del horario disponible y da ~1.8-1.9
  operaciones/día — no hace falta tocar nada. **Probado y descartado:**
  extenderla a todo el horario disponible (NY 03:30-16:00) sube la
  frecuencia a ~2.6-2.8/día pero **empeora el PF de forma clara** (train
  2.08→1.49, test 2.29→1.46) — confirma que el edge está en la sesión NY
  específicamente, no en tener más horas de mercado abierto.
- **15m**: no tiene ventana horaria (corre 24h) — si se llega a operar
  este timeframe, acotarlo a NY 03:30-16:00 (mismo horario de arriba)
  pierde sólo 13 de 137 operaciones (9%) y el PF de test **mejora**
  (1.35→1.52); train baja un poco (1.46→1.32) pero sigue siendo la
  config más consistente de las dos. Casi sin costo, recomendable si se
  usa 15m en paralelo con 5m para no perderse señales fuera de horario.

## Limitaciones a tener en cuenta

- Si en la misma vela se tocan SL y TP, el motor asume que el SL se ejecutó
  primero (supuesto conservador). El Strategy Tester de TradingView tiene la
  misma ambigüedad salvo que actives "bar magnifier".
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
- Una vez que tengas una combinación ganadora acá, hay que llevarla de vuelta
  a los inputs del `.pine` y confirmarla corriendo el Strategy Tester (o
  Deep Backtesting) de TradingView sobre esos mismos datos, como control
  cruzado antes de operarla en real.
