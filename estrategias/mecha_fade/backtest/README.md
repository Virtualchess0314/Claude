# Backtester de Mecha Fade

Réplica en Python de `mecha_fade.pine`. Misma metodología que
ifvg_sniper/upf_artillery: partición train/test en el tiempo, ranking
por resultado consistente entre ambos tramos, filtro de mínimo de
operaciones para no confiar en muestras chicas.

## Definición de los 3 indicadores

La estrategia original ("3 indicadores -AlphaTrend, Pivot y DIY-
buscando cada que el precio haga una mecha por fuera, entro al cierre
buscando el movimiento opuesto") tenía un hueco: no existe una fórmula
pública estándar llamada "DIY". Se resolvió compartiendo el código del
indicador real (`DIY Custom Strategy Builder [ZP] - v1`), y el usuario
confirmó que de ahí **sólo usa la función de soportes/resistencias**
(Supply/Demand Zone) — no el motor de ~35 "leading indicators" +
confirmaciones que trae el resto del script. Con eso, los 3 indicadores
quedaron así:

- **AlphaTrend**: indicador externo, no forma parte del script "DIY".
  Fórmula pública estándar (ratchet ATR/RSI o ATR/MFI).
- **Pivot**: **no son floor pivots de piso** (primer intento, descartado
  tras ver una captura del gráfico real) sino el indicador público
  **"Pivot Point SuperTrend"** — pivotes de swing (`ta.pivothigh`/`low`)
  promediados en una línea "center" con suavizado recursivo, envuelta en
  un trailing stop tipo SuperTrend sobre ATR. Es la línea roja/verde
  escalonada que cambia de lado cuando el precio la cruza.
- **DIY**: la función "Supply/Demand Zone" del script ZP — cajas de
  soporte/resistencia ancladas a los últimos swing high/low, con un
  buffer de ATR(50) y ruptura tipo BOS (la zona se desactiva cuando el
  cierre la cruza). El "punto rojo" que se ve en el gráfico es el
  marcador de POI (punto medio) de una zona recién formada — aparece
  como un punto porque la caja nace angosta.

**Simplificación documentada sobre DIY**: el script original mantiene
un historial de hasta 20 zonas por lado, con varias activas en
simultáneo si no se solapan. Acá se sigue sólo la **más reciente** por
lado (se reemplaza al aparecer un swing no solapado — más lejos que
`diy_overlap_atr_mult × ATR` del punto medio de la zona vigente — y se
desactiva al romperse). Para el propósito de esta señal alcanza con la
zona vigente; no se replicó el historial completo.

## ⚠️ Puntos abiertos — confirmar antes de confiar en cualquier resultado

Quedan 2 cosas sin definir que **cambian el resultado del backtest**:

1. **¿Los 3 indicadores tienen que coincidir a la vez, o alcanza con
   cualquiera?** Se implementó configurable (`confluence_need`, 1 a 3),
   default = 1 (cualquiera de los 3 dispara), porque "cada que" sugiere
   que cada indicador funciona como gatillo individual. Si la idea real
   es "los 3 alineados", correr con `--confluence-need 3`.
2. **Regla de salida (SL/TP).** No estaba especificada. Se usó el
   supuesto más natural para un fade de mecha: SL más allá del extremo
   de la mecha que disparó la señal (+ buffer en ATR, `sl_buffer_atr`)
   y TP a un múltiplo R de ese riesgo (`tp_r_mult`). Ambos son
   parámetros de barrido en `optimize.py` — si en realidad salís de otra
   forma (ej. al tocar el indicador contrario, o con trailing), avisame
   y se ajusta el motor.

## Qué hace el motor

- **AlphaTrend**: fórmula pública estándar (ratchet de `low - ATR*mult`
  en régimen alcista según RSI/MFI ≥ 50, de `high + ATR*mult` si no).
  Usa RSI por default; si el CSV trae volumen y se activa
  `at_use_volume`, usa MFI en su lugar (igual al toggle "non-crypto
  pairs" del indicador original).
- **Pivot**: Pivot Point SuperTrend (`pivot_point_supertrend()`) — ver
  definición arriba. Una única línea (como AlphaTrend) que actúa de
  soporte o resistencia según el trend vigente.
- **DIY**: zona de Supply/Demand (`supply_demand_zones()`) — ver
  definición arriba.
- **Señal**: para cada nivel/zona, "mecha por fuera + cierre adentro" ->
  soporte roto momentáneamente = long, resistencia rota
  momentáneamente = short. Si en la misma vela hay señal long Y short
  (indicadores contradictorios), se descarta -no hay forma de saber
  cuál "ganaría" intrabar con datos OHLC.
- **Entrada**: a mercado en el cierre de la vela de señal (con
  slippage). **Salida**: SL (stop, con slippage) o TP (límite, sin
  slippage), lo que se toque primero -si empatan en la misma vela, se
  asume que el SL se ejecuta primero (conservador).
- **Tamaño de posición**: ajustado para arriesgar siempre
  `max_risk_usd` (igual criterio que ifvg_sniper), tope en `max_qty`
  contratos.
- **Sesión**: cierre forzado a `session_close_hour:session_close_minute`
  hora de Nueva York (desactivable con `--no-session-close` para
  FX/cripto 24h), tope opcional de operaciones/día.
- **Costos**: comisión round-turn por contrato ($3.50 default,
  confirmado Tradovate/Tradeify en los otros dos proyectos) + slippage
  en ticks.

## Uso

```bash
pip install -r requirements.txt

python3 optimize.py tus_datos.csv
python3 optimize.py tus_datos.csv --tp-r-mult 1.0,1.5,2.0 --confluence-need 1,2,3 --sl-buffer-atr 0.05,0.10,0.20
python3 optimize.py tus_datos.csv --diy-swing-length 5,10,15 --diy-box-width 1.5,2.5,3.5

# Calidad de cada indicador por separado (antes de gastar tiempo en
# confluencias: si ninguno individual tiene ventaja, combinarlos
# tampoco la va a crear).
python3 analyze_source_quality.py tus_datos.csv
python3 analyze_source_quality.py datos_5m.csv datos_15m.csv  # varios timeframes juntos

# Curva de winrate/profit factor por múltiplo de R (1:1 a 1:10)
python3 analyze_r_multiple_curve.py tus_datos.csv
python3 analyze_r_multiple_curve.py tus_datos.csv --sl-buffer-atr 0.05,0.10,0.20 --fresh-only

# Post-mortem de operaciones perdedoras: ¿stop muy ajustado (barrido
# tardío) o ruptura real (breakout)?
python3 analyze_loss_postmortem.py tus_datos.csv
python3 analyze_loss_postmortem.py tus_datos.csv --only-source at --at-mult 2.0 --sl-buffer-atr 1.0 --tp-r-mult 2.0

# Desglose de resultado por sesión (Asia/Londres/Londres-NY/NY)
python3 analyze_session_performance.py tus_datos.csv
python3 analyze_session_performance.py tus_datos.csv --only-source at --at-mult 2.0 --sl-buffer-atr 1.0 --tp-r-mult 2.0

# Salida por trailing en vez de TP fijo
python3 optimize.py tus_datos.csv --trailing-exit-source piv
```

`Params.entry_start_hour`/`entry_end_hour` (hora de NY, 0-24, admite
ventanas que cruzan medianoche) restringe cuándo se permite ABRIR una
operación nueva — no afecta salidas de posiciones ya abiertas. Expuesto
como `--entry-start-hour`/`--entry-end-hour` en `optimize.py`.

`Params.only_source` (`'at'`/`'piv'`/`'diy'`/`None`) aísla un solo
indicador para comparar su calidad individual, ignorando
`confluence_need` — es una herramienta de análisis, no parte de la
estrategia original.

`Params.fresh_only` (`--fresh-only` en los scripts de arriba): filtro
de frescura — sólo cuenta la primera mecha que testea cada nivel/zona
desde que nació (último flip de régimen para AlphaTrend/Pivot, nueva
zona para DIY); repeticiones contra el mismo nivel se ignoran.
**Probado sobre datos reales y NO recomendado como default** — mejora
el resultado en train pero lo empeora consistentemente en test sobre
la única configuración con ventaja real conocida (ver
`runs/2026-09-17_fresh_only_filter.txt`). Queda disponible para seguir
explorando.

**Aviso importante sobre confluencia real**: con los parámetros
default, AlphaTrend dispara señales muchísimo más seguido que Pivot
(en MNQ 5m, ~1370 vs ~130 en 2 meses) y casi nunca coinciden en la
misma vela (~8 velas en común) — el ancho de banda de Pivot Point
SuperTrend (`piv_atr_factor`) es mucho mayor que el de AlphaTrend
(`at_mult`). Esto hace que `confluence_need=2` o `3` casi no generen
operaciones con los defaults. Si la idea es que los 3 indicadores
confluyan de verdad, hay que acercar sus anchos de banda (subir
`at_mult` y/o bajar `piv_atr_factor`) o aceptar que `confluence_need=1`
es el único valor práctico por ahora.

El CSV es el export de TradingView ("Export chart data") con al menos
`time,open,high,low,close` — `volume` es opcional.

## Hallazgos hasta ahora (sobre datos reales de MNQ)

- Con parámetros default, **ningún indicador individual ni la
  confluencia cruda tienen ventaja** en ninguna de las 5 temporalidades
  probadas (1m/2m/5m/15m/240m) — ver `runs/2026-09-17_source_quality.txt`
  y `runs/2026-09-17_r_multiple_curve.txt`.
- **Mejor configuración encontrada hasta ahora: AlphaTrend solo, 15m,
  `at_mult=2.0`, `sl_buffer_atr≈1.0`, `tp_r_mult≈2.0-2.5`** — profit
  factor 1.3-1.6 en train y test de forma consistente (ver
  `runs/2026-09-17_loss_postmortem.txt`). Reemplaza al hallazgo previo
  de `sl_buffer_atr=0.2-0.3` (PF~1.0-1.1) — el stop original era
  demasiado ajustado.
- **Mejora adicional: salida por trailing de Pivot en vez de TP fijo**
  (`trailing_exit_source='piv'`) — sobre la misma configuración, casi
  duplica la expectativa (PF 1.87, exp_r +0.42R vs PF 1.45, +0.22R con
  TP fijo). SL chico al entrar + quedarse montado mientras Pivot (banda
  ancha, tolera ruido) siga sosteniendo la tendencia, en vez de un
  target fijo. Trailing con AlphaTrend (la misma línea que entra) NO
  funciona -te saca por ruido, winrate cae a ~17-26%. Ver
  `runs/2026-09-17_trailing_exit.txt`.
- **⚠️ Esa ventaja es específica de 15m, no se generaliza.** Repetido
  el mismo barrido de R-múltiplo (incluyendo RR negativo, 1:0.25 a
  1:1) en 1m/2m/5m: el profit factor de test NUNCA cruza 1.0 en ningún
  R probado, y encima mejora en la dirección OPUESTA a 15m (en 15m el
  PF sube con R hasta 2.5; en 1m/2m/5m baja). 240m no tiene muestra
  suficiente (7 operaciones en 6.5 años). Esto baja la confianza de
  "ventaja de mercado real" a "posible ajuste a esta ventana de 5
  meses de 15m" — ver `runs/2026-09-17_r_multiple_by_timeframe.txt`
  antes de operar esta configuración en real.
- **Backtesting por sesión**: la sesión NY (12-17 ET) rinde
  sistemáticamente mejor y el overlap Londres/NY (08-12 ET)
  sistemáticamente peor, de acuerdo en las 5 temporalidades — patrón
  robusto. Sobre la mejor configuración, excluir el overlap
  (`--entry-start-hour 12 --entry-end-hour 8`) mejora el profit factor
  de forma modesta (1.45→1.49) sin sacrificar mucha muestra. Restringir
  sólo a NY da un PF vistoso (1.75) pero con apenas 25 operaciones —
  no confiar todavía. Ver `runs/2026-09-17_session_performance.txt`.
- El filtro de frescura (`fresh_only`) empeora esa configuración en
  test de forma sistemática — no usar por ahora (ver
  `runs/2026-09-17_fresh_only_filter.txt`).
- **Post-mortem de operaciones perdedoras**: entre el 49% y el 78% de
  las pérdidas (según timeframe) son por stop demasiado ajustado — el
  precio SÍ termina yendo hacia donde apuntaba el fade original, sólo
  que después de que el stop ya nos había sacado. El resto (22-39%) son
  rupturas reales (85-95% coinciden con un flip de régimen confirmado
  del indicador). Ver `runs/2026-09-17_loss_postmortem.txt` — de ahí
  sale el hallazgo de ensanchar `sl_buffer_atr` de arriba. Ojo: ese
  ensanche ayuda mucho en la configuración ya afinada (AlphaTrend solo)
  pero NO de forma clara en la señal cruda sin tunear.
- La hipótesis "un flip de AlphaTrend mata al Pivot Point SuperTrend
  vigente" se sostiene con fuerza (67-85% según filtro de ruido) y de
  forma muy consistente entre timeframes — ver
  `runs/2026-09-17_alphatrend_kills_pivot.txt`. **⚠️ Corregido después
  (ver bullet de "corrección de fondo" más abajo): con la definición
  correcta de "flip" el kill rate baja a ~50%**, básicamente al azar.
- **⚠️ "Matar" el pivot NO anticipa un recorrido grande — es al revés.**
  Se midió el MFE tras cada flip de AlphaTrend, separando tramos donde
  el pivot queda muerto vs donde se lo vuelve a tocar (retest) antes de
  seguir. Consistente en las 5 temporalidades: los tramos que MATAN el
  pivot recorren poco (mediana ~0.55-0.64R, sólo 23-31% llega a 1R) —
  impulsos que se agotan rápido. Los que SÍ retestean antes de continuar
  recorren mucho más (mediana ~3.5-4R, ~90-97% llega a 1R, ~65-79%
  llega a 2R) — patrón "breakout + retest" clásico. No construir una
  entrada de "flip + kill esperando movimiento grande" — sería apostar
  justamente al caso de recorrido chico. Ver
  `runs/2026-09-19_post_kill_runup.txt` (números actualizados tras la
  corrección de fondo). Posible siguiente paso, no backtesteado
  todavía: entrada confirmada recién cuando el precio retestea el pivot
  roto, en vez de fade o de apostar al kill.
- **Segunda estrategia (continuación, no fade): entrar directo al flip
  de AlphaTrend.** Con TP fijo o trailing de Pivot por valor de línea no
  funciona (aguanta operaciones larguísimo sin cortar las malas). Un
  primer barrido pareció mostrar ventaja consistente en 1m/2m/5m, pero
  era un artefacto de look-ahead bias en el filtro de "flip genuino"
  (corregido con `causal_confirmed_flips` / `--confirm-bars`) — con esa
  corrección sola, 0-1 de 442 combos pasaban train_pf>1 y test_pf>1.
  **Con la corrección de fondo de la definición de "flip" (ver bullet
  siguiente) + SL de ATR puro (no apoyado en el pivot, que ahora queda
  del lado equivocado ~46% de las veces) + salida por régimen** (no por
  valor de línea), reaparece señal: **8 de 120 combinaciones pasan
  train_pf>1 Y test_pf>1, y las 8 son de 1m** con distintos valores de
  SL y tipos de salida — más compatible con un efecto real y modesto en
  1m que con una casualidad aislada, aunque 8/120 (6.7%) sigue sin
  poder descartar del todo el azar. Ejemplo: 1m, `--sl-atr-mult 3.0
  --tp-r-mult 1.0`: train PF=1.32 (n=117, +0.14R), test PF=1.70 (n=53,
  +0.23R). Ningún combo de 2m/5m/15m/240m pasó. Ver
  `runs/2026-09-19_alphatrend_flip_entry.txt` y
  `analyze_alphatrend_flip_entry.py`.
- **⚠️ Corrección de fondo (encontrada gracias al usuario comparando
  contra su gráfico real): la definición de "flip de AlphaTrend" estaba
  mal.** Se definía como "cierre por encima/debajo de la línea", pero el
  indicador público real colorea la nube según la PENDIENTE de la línea
  (AlphaTrend[i] vs AlphaTrend[i-2]), no según dónde esté el precio.
  Verificado con datos reales: la definición vieja daba 5x más "flips"
  que la real (1372 vs 283 en un archivo de 5m), incluyendo señales que
  en el gráfico real no existen. **No afecta a mecha_fade** (la
  estrategia original wick-fade sólo usa el VALOR de la línea, nunca el
  concepto de régimen/color) — sí afectaba a los tres puntos de arriba
  (kills_pivot, post_kill_runup, flip_entry), todos corregidos y
  re-corridos. Ver `runs/2026-09-19_alphatrend_regime_fix.txt` para el
  detalle completo de la corrección y su impacto.
- **🏆 Filtro de color de pivot (idea del usuario) — el hallazgo más
  fuerte de todo el proyecto.** El Pivot Point SuperTrend tiene su
  propio color/trend (soporte/resistencia), independiente del color de
  la nube. Sólo cuenta "matar" un pivot de color CONTRARIO a la nube
  nueva (el remanente de la tendencia vieja) — los de mismo color ya
  están alineados, no es el mismo fenómeno, y antes se contaban
  mezclados. Backtesteado como filtro de entrada real
  (`--require-opposite-color-pivot`): **15/90 combinaciones pasan
  train_pf>1 Y test_pf>1 (16.7%, vs ~5-7% esperable por azar)**,
  repartidas en 4 temporalidades (1m: 9/30, 2m: 2/24, 5m: 3/24, 15m:
  1/12) — mucho más robusto que cualquier hallazgo anterior de esta
  línea de investigación (antes concentrado en una sola temporalidad).
  Mejor ejemplo: 1m, `sl_atr_mult=4.0, tp_r_mult=1.0`: train PF=1.47
  (n=62, +0.15R), test PF=1.42 (n=35, +0.11R). Ver
  `runs/2026-09-19_opposite_color_pivot_filter.txt`. Pendiente: ampliar
  muestra y revisar sensibilidad a costos antes de operar.
  **⚠️ ACTUALIZACIÓN: validado out-of-sample con un archivo nuevo (NQ
  1m, mismo instrumento, ventana de calendario distinta) y el resultado
  NO se sostiene** — de los 9 combos de 1m que pasaban train Y test,
  sólo 1 mantiene PF>1 en el período nuevo, y la expectativa promedio de
  esos 9 pasa a ser NEGATIVA (-0.116R). El archivo de 1m sólo cubre ~12
  días de calendario por período — demasiado poco para fijar parámetros
  finos de SL/TP con confianza; combos "ganadores" en un período de 12
  días pierden su ventaja casi por completo en el siguiente. La entrada
  en sí (idea del filtro de color de pivot) sigue siendo razonable, pero
  ningún combo específico de parámetros debería tratarse como validado
  todavía. Ver `runs/2026-09-20_oos_validation_nq.txt`.
  **⚠️ CORRECCIÓN DE FONDO: `max_risk_usd` era $150 por default, el
  usuario aclaró que el límite real es $750.** No es cosmético -destapó
  un bug de sizing: con $150 y `point_value_usd=2.0`, cualquier trade
  con riesgo >75 puntos quedaba con `qty=0` y se descartaba en
  silencio (afecta sobre todo a temporalidades altas -por esto 240m
  "nunca tenía muestra suficiente", algo ya notado pero no
  diagnosticado). Corregido en `engine.Params` y los `--max-risk-usd`
  de `optimize.py`/`analyze_alphatrend_flip_entry.py`. Re-corrido el
  barrido completo en las 5 temporalidades: **15/138 (10.9%, antes
  15/90=16.7% porque 240m quedaba afuera)** — sigue sobre el azar pero
  con menos margen en 1m-15m (9→6, 2→1, 3→1, 1→0); a cambio, **240m
  pasa a tener muestra y aporta 7/18 combos**, con PF más modesto
  (1.02-1.35) pero sobre 6.5 años de historia real -mucho más creíble
  que los combos de 1m dada la fragilidad ya encontrada en la
  validación out-of-sample. Recomendado priorizar 240m/15m sobre 1m de
  acá en más. Ver `runs/2026-09-20_max_risk_usd_correction.txt`.
- **⚠️ Corrección: "matar" el pivot es que el PIVOT MISMO flipea de
  color, no que el precio vuelve a tocar tal nivel** (aclarado por el
  usuario). El Pivot Point SuperTrend cambia de color cuando el cierre
  cruza su banda -eso es "morir/convertirse", no "volver a tocar un
  valor congelado" (la línea del pivot se mueve vela a vela). Corregido
  en `test_ppst_pivot_kill()` y `measure_runups()`. Con la definición
  correcta, el resultado es mucho más limpio e intuitivo: el kill rate
  vuelve a ~63-74% (cerca del hallazgo original del usuario), y la
  separación por recorrido es casi perfecta — cuando el pivot SÍ flipea
  (confirma la nueva tendencia), el recorrido siempre llega a 1R
  (99-100%, mediana 4.5-5R); cuando se mantiene terco (nunca confirma),
  casi nunca pasa de 1R (17-28%, mediana 0.5-0.65R). Consistente en las
  5 temporalidades. Esto valida el mecanismo que el usuario tenía en
  mente: el pivot confirmando la tendencia ES la señal de que el
  movimiento va a correr. Ver `runs/2026-09-19_pivot_flip_definition_fix.txt`.
- **⚠️ Pero entrar recién cuando el pivot confirma NO funciona como
  regla operable (`--pivot-confirmation`) — CERRADO.** Backtesteado con
  SL apoyado en el pivot recién nacido: 0/35 combinaciones pasan en 1m
  (única temporalidad con muestra suficiente; 2m/5m/15m/240m casi sin
  operaciones porque el motor sólo aguanta una posición a la vez y estas
  operaciones duran ~59 velas, similar al intervalo entre señales).
  Motivo de fondo: el MFE grande (bullet anterior) se mide desde el flip
  ORIGINAL de AlphaTrend; para cuando el pivot confirma, parte del
  movimiento ya pasó, y el riesgo nuevo (más chico) no compensa lo que
  queda por delante. "El episodio completo fue grande" no es lo mismo
  que "entrar en la confirmación da un movimiento grande desde ahí". El
  hallazgo que SÍ sigue en pie es el del bullet de arriba: filtrar por
  color de pivot AL MOMENTO del flip (sin esperar confirmación) — ese
  entra al principio del movimiento, no después. Ver
  `runs/2026-09-19_pivot_confirmation_entry.txt`.
- **⚠️ Secuencia exacta del usuario (entrar al flip, SALIR cuando el
  pivot confirma) — tampoco funciona, CERRADO.** Probadas las dos
  variantes de "ahí ya podríamos cerrar con TP o dejarlo correr":
  cerrar directo al confirmar (`trailing_exit_source='pivot_confirms'`,
  0/20 combos) y mover SL a breakeven + dejar correr hasta que la NUBE
  se revierta (`'pivot_confirms_then_trail'`, 1/20, peor que las
  variantes ya validadas). Motivo: el pivot confirma rápido (~8 velas
  en 1m), mucho antes de que se desarrolle el recorrido grande medido
  en el bullet anterior (que va hasta el PRÓXIMO flip de AlphaTrend,
  ~30-40 velas después) — cerrar o mover el SL ahí deja la mayor parte
  del recorrido sin capturar. El mejor resultado de toda esta línea de
  investigación sigue siendo el ya validado arriba: entrar con filtro
  de color de pivot + salida por TP fijo o por reversión de la NUBE
  (no del pivot). Ver `runs/2026-09-19_pivot_confirms_as_exit.txt`.
- **⚠️ Confirmación de la temporalidad siguiente — sin evidencia
  suficiente en NINGUNA variante, CERRADO.** Idea del usuario: con la
  entrada ya validada, mirar la temporalidad más alta y dar más
  recorrido (sin TP, trailing por reversión de nube) si el precio
  también supera su nube ahí; si no, TP conservador. Probado con pares
  "consecutivos" de datos nativos (1m→2m, 2m→5m, 5m→15m, 15m→240m):
  3/56 combinaciones (5.4%, apenas sobre el azar), concentradas en un
  solo par y con perfil de sobreajuste (train mucho mejor que test).
  Probado de nuevo con la escalera clásica completa (1-2-3-5-10-15-30-45-60,
  re-muestreando donde no había CSV nativo): **1/80 (1.25%, por debajo
  del azar)** en los pares hasta 30m, un único hit aislado sin
  acompañamiento de ningún par vecino; agregado 45m para completar el
  tramo 30-60, los pares 30m→45m y 45m→60m dan **0 operaciones** en
  ambos (ni siquiera hay muestra para medir con los ~5 meses de datos
  de base). En todas las rondas, el par con más muestra y mejor
  resultado sin este filtro (entradas en 1m) da 0/16 — agregar la
  confirmación de temporalidad alta no mejora nada, empeora el
  resultado ya conocido. Ver `runs/2026-09-19_htf_confirmation.txt` y
  `analyze_htf_confirmation.py`.
- **⚠️ Distancia pivot-precio al momento del flip — observación real,
  sin uso práctico, CERRADO.** Idea del usuario: si el pivot está
  exageradamente lejos del precio al momento del flip, es más probable
  que otro flip de AlphaTrend resetee el tramo antes de que el precio lo
  "busque". Confirmado como fenómeno estadístico: correlación negativa
  y consistente en las 5 temporalidades entre distancia (en ATR) y
  probabilidad de que el pivot se mate (r=-0.19 a -0.38). Pero la caída
  de "MFE en R" que acompaña a esa distancia es mayormente un artefacto
  mecánico (R usa la distancia como denominador) — controlando con MFE
  en ATR, la correlación colapsa a casi cero (-0.14 a +0.13): el
  movimiento absoluto de precio no depende realmente de esa distancia.
  Probado como filtro real (`--max-pivot-distance-atr`) sobre el mismo
  barrido de 90 combos que dio 15 pasan: un umbral laxo (3x ATR) no
  cambia nada (siguen pasando 15), umbrales más agresivos (2x/1.5x/1x)
  sólo recortan muestra y bajan el resultado a 9/9/0 — no mejora la
  estrategia ya validada. Ver `runs/2026-09-20_pivot_distance_vs_kill.txt`
  y `analyze_pivot_distance_vs_kill.py`.
- **Winrate, ratios R:R<1 y filtro de sesión horaria en la entrada al
  flip.** Winrate de los 15 combos que pasan: 30-61% según el TP usado
  (coherente con cada R:R, sin sorpresas; avg_loss≈-1.0R por costos).
  Probar ratios R:R por debajo de 1 (TP 0.5R/0.75R, arriesgar más de lo
  que se busca ganar): **no ayuda, 2/30 (6.7%, cerca del azar)** — se
  cierra sin evidencia. **🏆 Filtro de sesión NY (12-17 ET) — hallazgo
  prometedor pero NO confirmado.** El mismo patrón que ya se veía en la
  estrategia original (`runs/2026-09-17_session_performance.txt`) se
  repite acá en las 4 temporalidades: NY sistemáticamente la mejor
  sesión, Londres (03-08) la peor/negativa. Restringiendo TODAS las
  entradas a esa ventana (`--entry-start-hour 12 --entry-end-hour 17`,
  agregado a `simulate_flip_entries`, ya existía en `engine.Params` pero
  no estaba conectado acá) el pass rate del barrido de 90 combos sube de
  15/90 (16.7%) a **19/40 (47.5%)**, concentrado sobre todo en 1m
  (18/28) con PF de hasta 2-3 y expectativas de +0.6R/operación. ⚠️ Con
  una salvedad seria: el archivo de 1m sólo cubre ~12 días de calendario
  — la mejora es demasiado grande para no sospechar sobreajuste con tan
  poca muestra de calendario, y en 2m/5m/15m casi no queda muestra
  evaluable con esta restricción (6/6/0 combos). Recomendado: conseguir
  más meses de datos de 1m/2m/5m antes de tratar esto como mejora real,
  no sólo como dirección de investigación válida. Ver
  `runs/2026-09-20_session_winrate_rr_check.txt`.
  **⚠️ La sospecha se confirmó**: validado con un archivo de NQ 1m
  nuevo (misma ventana de 12 días pero fechas distintas), el "ganador"
  claro cambia — Asia pasa de 2da mejor sesión a la peor, y el overlap
  Londres/NY empata a NY como mejor sesión. Sólo Londres (03-08) se
  mantiene mala en ambos períodos. La dirección general (evitar Londres,
  preferir NY/overlap) sobrevive a medias; el detalle fino, no. Ver
  `runs/2026-09-20_oos_validation_nq.txt`.
- **🏆 Metodología ICT CRT (Candle Range Theory) — explica el mecanismo,
  AL REVÉS de lo esperado, y mejora sustancialmente el filtro de
  entrada.** CRT = patrón de 3 velas (rango / manipulación-barrido de
  liquidez / distribución-reclamo, `engine.detect_crt()`). Contra la
  intuición, un flip de AlphaTrend CON un CRT de respaldo reciente tiene
  kill rate MÁS BAJO que uno sin él (en 3 de 5 temporalidades, robusto a
  distintas ventanas de 3/5/7 velas) — un quiebre "limpio" sin trampa de
  liquidez previa parece más decisivo que uno que vino precedido de un
  barrido. Usado como filtro real (`--crt-filter exclude`, sólo entra si
  NO hubo CRT reciente): el pass rate del barrido de 138 combos sube de
  15 (10.9%) a **34 (24.6%)**, repartido en 1m (15/30), 2m (7/30) y
  **240m (12/18, 67%)** — con expectativa de TEST más alta que la de
  TRAIN en promedio, no el patrón típico de sobreajuste. Validado
  out-of-sample con el archivo NQ 1m: la mejora en 1m NO sobrevive
  (1/15 vs 0/15 del baseline, ambos negativos) — mismo diagnóstico de
  fragilidad ya conocido para 1m. El resultado de 240m (6.5 años de
  historia) es el más creíble del proyecto a la fecha. Ver
  `runs/2026-09-20_crt_vs_kill.txt`.

## Qué mirar en el resultado

Mismo criterio que los otros dos backtesters del repo: priorizar
consistencia train/test sobre el mayor profit factor aislado,
desconfiar de combinaciones con pocas operaciones en test, y mirar el
drawdown máximo en dólares reales antes de emocionarse con el profit
factor. Además, acá en particular: correr por separado con
`confluence_need=1` vs `2` vs `3` para ver si la confluencia total
realmente mejora la calidad de las señales o sólo reduce el volumen sin
subir el profit factor.

## Limitaciones a tener en cuenta

- Los 2 puntos abiertos de arriba (confluencia y regla de salida).
- La simplificación de "una sola zona activa por lado" en DIY (ver
  definición arriba).
- Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
  primero (supuesto conservador, igual que en ifvg_sniper/upf_artillery).
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
- Los pivotes/swings se replican de forma vectorizada (ventana rodante)
  — no repintan, coinciden con el comportamiento real de Pine.
