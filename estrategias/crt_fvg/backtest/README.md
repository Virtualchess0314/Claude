# Backtester de CRT + FVG

Réplica en Python de **Candle Range Theory (CRT)**, un concepto ICT, con
la entrada afinada por Fair Value Gap en un timeframe menor. Ver
docstring de `engine.py` para el detalle completo de la mecánica
(barrido C1→C2 en el timeframe mayor, ventana de confirmación, FVG en el
timeframe menor como gatillo de entrada, TP fijo = extremo opuesto del
rango de C1).

Es la tercera variante de estrategia basada en FVG explorada en este
repo, después de `ifvg_sniper` (reversión, validada y en uso) y
`fvg_continuation` (continuación, descartada — ver su README).

## Estado: sin edge confirmado, pero la más prometedora de las variantes FVG probadas

Se probaron 3 combinaciones de timeframe mayor/menor sobre MNQ, todas
con split train/test 70/30 (ver `runs/2026-09-06_*.csv`):

| Par (LTF/HTF) | Muestra (test) | PF test | Lectura |
|---|---|---|---|
| 15m / 1h (HTF derivado por resample) | 15-21 operaciones | 0.88-1.08 | En el punto de equilibrio — la señal más confiable de las tres por tener más muestra, pero sin edge probado |
| 5m / 15m (ambos nativos) | 22-52 operaciones | 0.42-0.65 | Claramente negativo |
| 15m / 4h (HTF derivado por resample) | **sólo 5-11 operaciones** | 1.50-2.60 | Se ve muy bien pero la muestra es demasiado chica para confiar — con 5 meses de historial, un rango de 4h da muy pocos setups. Cualquier par de trades que cambie de resultado da vuelta el número entero. |

**Ninguna combinación tiene todavía una muestra grande Y un profit
factor por encima de 1 al mismo tiempo.** El caso de 4h es el más
interesante para seguir explorando, pero necesita mucho más historial
(varios años de datos nativos de 1h/4h, no derivados de 15m) antes de
que esa muestra de 5-11 operaciones se vuelva algo confiable.

## Próximo paso sugerido

Conseguir más historial nativo (ideal: 1-2 años de MNQ en 15m o 5m, o
directamente velas nativas de 1h/4h) para poder correr el barrido de
HTF=4h con una muestra que valga la pena analizar. Con los datos
actuales (5 meses de 15m) no alcanza.

## Uso

```bash
pip install -r requirements.txt
python3 optimize.py ruta/a/tus_datos_LTF.csv --htf-rule 1h,4h --max-wait-htf-bars 1,2,3,4
```

El CSV pasado es el timeframe MENOR — el mayor se deriva por resample
adentro del motor (`data.resample_ohlc`), así que `--htf-rule` tiene que
ser múltiplo exacto del timeframe del CSV cargado. Ver `data.py` para el
formato esperado (export de TradingView, igual que el resto del repo).
