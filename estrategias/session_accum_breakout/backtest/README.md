# Session Accumulation Breakout — backtester

Estrategia descrita por el usuario ("la vi de un trader"), sin fuente
exacta identificada (se buscó en la web, ver `runs/2026-09-20_busqueda_fuente.txt`
-parece una combinación de conceptos conocidos: Opening Range Breakout +
pyramiding con riesgo constante, no una estrategia "de marca" indexada).
Formalizada e implementada en Python directamente a partir de la
descripción, sin script .pine (no viene de un indicador de TradingView).

## Reglas (y los supuestos explícitos que hicieron falta para programarlas)

1. **Acumulación**: ventana de `--lookback-hours` horas antes de la
   apertura de una sesión (`--session-open-hour`, hora de NY), dividida
   en dos fases (`--confirm-fraction`, default 0.5 = mitad y mitad):
   - **Formación**: arma un rango (high/low) con las velas de la
     primera mitad de la ventana.
   - **Confirmación**: el rango de la fase anterior queda CONGELADO: se
     exige que el precio se mantenga adentro durante el resto de la
     ventana, hasta la apertura.
   ⚠️ Supuesto: un primer intento comparaba cada cierre contra el rango
   que se estaba armando EN VIVO (sin congelar) — invalidaba casi todos
   los días reales, porque cualquier consolidación hace nuevos
   máximos/mínimos todo el tiempo mientras se forma. Separar formación
   de confirmación fue necesario para que la regla sea implementable.
2. **Invalidación**: si durante la fase de confirmación el precio
   CIERRA por fuera del rango ya congelado, el setup de ese día queda
   invalidado -no se opera. Es la lectura literal de "si el rango se
   rompe antes de iniciar la sesión no es válido".
3. **Entrada**: si el rango aguanta hasta la apertura, se vigila la
   ruptura desde ahí en adelante -primera vela que CIERRA por fuera del
   rango (el de la fase de formación) dispara la entrada a mercado.
4. **SL inicial**: en el extremo OPUESTO del rango + buffer de ATR.
   Supuesto: si el precio recorre todo el rango en contra, el setup
   falló -es el nivel de invalidación estructural más natural.
5. **Promediadas (pyramiding)**: cada vez que el precio avanza
   `--add-step-atr-mult` x ATR más allá de la ÚLTIMA entrada, se agrega
   otra entrada de la MISMA cantidad de contratos (hasta `--max-adds`).
   El SL de TODA la posición se recalcula en cada agregado para que el
   riesgo TOTAL siga siendo exactamente `--max-risk-usd` -esto obliga a
   mover el SL más cerca del precio a medida que se agranda la posición
   (lectura literal de "el SL se ajusta cada vez que se promedia para
   arriesgar siempre lo mismo").
6. **TP**: se recalcula junto con cada promediada para que, si se
   alcanza, la ganancia total sea `--tp-r-mult` (default 4.0, el "RR
   1:4" pedido) veces el riesgo fijo.
7. **Cierre**: por SL, por TP, o forzado si se llega al comienzo del
   PRÓXIMO ciclo de acumulación sin haber salido antes.

Puntos que el usuario NO especificó y quedaron como supuesto (a
confirmar si se quiere afinar):
- Cuántos contratos agregar en cada promediada (se asumió: los mismos
  que la entrada inicial).
- Cuánto debe avanzar el precio para "ganarse" una promediada (se
  parametrizó en múltiplos de ATR, barrido).
- Qué tan larga es la ventana de acumulación y cómo se define
  exactamente "romper antes de la sesión" (ver supuesto de
  formación/confirmación arriba).

## Resultado por temporalidad (barrido: session_open_hour x lookback_hours x max_adds x add_step_atr_mult, 192 combos)

Esta estrategia dispara COMO MUCHO 1 operación por día (por sesión
elegida) -muy distinta en frecuencia a mecha_fade. Esto significa que
necesita MUCHO más historial de calendario que el resto del proyecto
para juntar muestra -algo que se nota fuerte en los resultados:

| TF  | días de calendario | combos con muestra | pasan train_pf>1 y test_pf>1 |
|-----|---------------------|---------------------|-------------------------------|
| 1m  | ~12                 | 0                    | 0 (sin muestra ni mínima)     |
| 2m  | ~42                 | 36                   | 0                              |
| 5m  | ~54                 | 36                   | 0                              |
| 15m | ~150                | 144                  | **32 (22%)**                   |

**15m es la única temporalidad con muestra utilizable**, y ahí el
resultado es llamativo: hasta test PF=4.03 (n=8) y expectativa de
+1.49R/operación con `session_open_hour=3.0` (apertura de Londres),
`lookback_hours=2.0`. Pero los tamaños de muestra son MUY chicos (train
14-36, test 6-13) -mucho más chicos que cualquier otro hallazgo "que
pasa" en el resto del proyecto (mecha_fade partía de 90-138 operaciones
por combo; acá 32 combos que "pasan" tienen entre 6 y 13 operaciones de
test).

## ⚠️ Validación out-of-sample (archivo NQ 15m, remuestreado del NQ 1m)

Se resampleó el archivo NQ 1m (2026-09-06 a 09-18, la misma ventana
usada para validar mecha_fade) a 15m y se probaron los 13 mejores
combos de 15m: **11 de 13 mantienen PF>1**. Pero con **n=2-5
operaciones** cada uno en el período nuevo (12 días de calendario dan
muy pocos ciclos de "una vez por día" para esta estrategia) -esto NO es
una validación real, es una muestra demasiado chica para confirmar NI
para refutar nada. A diferencia de mecha_fade (donde la validación OOS
con NQ 1m sí tenía ~100+ operaciones y pudo desenmascarar la
fragilidad), acá la naturaleza de baja frecuencia de la estrategia hace
que ni 12 días de un archivo adicional alcancen para decir algo con
confianza.

## Conclusión

La estrategia está implementada y es coherente con la descripción del
usuario (con los supuestos documentados arriba). El resultado en 15m es
prometedor en la dirección esperada (PF alto, expectativa positiva,
consistente en muchas combinaciones de parámetros vecinas -no un único
pico aislado), pero la naturaleza de "máximo 1 trade/día" de esta
estrategia hace que la muestra disponible (150 días de 15m, la mejor
que hay) sea estructuralmente insuficiente para confiar en el
resultado con la misma disciplina que el resto del proyecto. Se
necesitarían AÑOS de historia intradía (no meses) para juntar una
muestra comparable en confiabilidad a la de mecha_fade.

**No se recomienda operar esto todavía.** Antes de confiar en cualquier
combinación de parámetros específica hace falta: (a) conseguir mucho
más historial de 5m/15m (ideal: 1-3 años), y (b) considerar si tiene
sentido testear la estrategia en MÁS de una sesión por día en paralelo
(actualmente cada `session_open_hour` se prueba por separado) para
multiplicar la frecuencia de señales sin diluir la lógica de la regla.

## Uso

```
python3 optimize.py datos.csv
python3 optimize.py datos.csv --session-open-hours 3.0 --lookback-hours 2.0 --confirm-fraction 0.5 --max-adds 0,1,2,3 --add-step-atr-mult 0.5,1.0,1.5
```

## Limitaciones a tener en cuenta

- Los 3 supuestos documentados arriba (regla de "rompe antes de la
  sesión", tamaño de cada promediada, distancia para promediar).
- Al igual que en el resto del repo: si SL y TP se tocan en la misma
  vela, se asume que el SL se ejecuta primero.
- No hay script .pine -esta estrategia no viene de un indicador de
  TradingView, se define directamente en el motor de Python.
- No probado con costos de comisión/slippage distintos a los defaults
  del proyecto ($3.50 round-turn, 1 tick de slippage).
