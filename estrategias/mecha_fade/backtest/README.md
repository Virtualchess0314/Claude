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
  `runs/2026-09-17_alphatrend_kills_pivot.txt`. Todavía no se probó
  como filtro real de la estrategia.
- **⚠️ "Matar" el pivot NO anticipa un recorrido grande — es al revés.**
  Se midió el MFE tras cada flip genuino de AlphaTrend, separando
  tramos donde el pivot queda muerto vs donde se lo vuelve a tocar
  (retest) antes de seguir. En las 5 temporalidades, de forma muy
  consistente: los tramos que MATAN el pivot recorren poco (mediana
  ~0.5R, sólo 18-23% llega a 1R, duran ~8-9 velas) — son impulsos que
  se agotan rápido. Los tramos que SÍ vuelven a tocar el pivot antes de
  continuar recorren mucho más (mediana ~2R, ~99% llega a 1R, ~50%
  llega a 2R, duran ~16-18 velas). Es el patrón clásico de "breakout +
  retest" siendo mejor que el breakout solo. No construir una entrada
  de "flip + kill esperando movimiento grande" — sería apostar
  justamente al caso de recorrido chico. Ver
  `runs/2026-09-19_post_kill_runup.txt`. Posible siguiente paso (no
  construido todavía): entrada al flip de AlphaTrend confirmada recién
  cuando el precio hace retest del pivot roto, en vez de fade o de
  apostar al kill.
- **Segunda estrategia (continuación, no fade): entrar directo al flip
  de AlphaTrend.** Con TP fijo o trailing de Pivot no funciona (aguanta
  operaciones larguísimo sin cortar las malas). Un primer barrido pareció
  mostrar ventaja consistente con SL chico + trailing de AlphaTrend en
  1m/2m/5m, pero **era un artefacto de look-ahead bias** en el filtro de
  "flip genuino" (decidía si un flip servía mirando el futuro). Corregido
  con un filtro causal (`analyze_alphatrend_flip_entry.py`,
  `causal_confirmed_flips` / `--confirm-bars`), **0 de 90 combinaciones
  pasan train_pf>1 Y test_pf>1** — por ahora, entrar directo al flip de
  AlphaTrend (con `confirm_bars=1`, entrada inmediata sin filtros extra)
  no muestra ventaja real. Ver `runs/2026-09-19_alphatrend_flip_entry.txt`
  (incluye la corrección completa). Pendiente: revisar si confirmar el
  flip con más de 1 vela (`--confirm-bars`, sigue siendo causal) cambia
  algo.

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
