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

**Ninguna combinación tenía todavía una muestra grande Y un profit
factor por encima de 1 al mismo tiempo.**

## Segunda vuelta: mecánica CRT pura sobre 6+ años de 4h nativo — sin edge confirmado

Se consiguió el export nativo de MNQ 4h (10.295 velas, 2020-01-01 a
2026-09-04 — mucho más historial que 15m/5m). Con esa muestra se puede
probar la mecánica CRT **sin** la capa de FVG en timeframe menor:
entrar a MERCADO en el open de la vela siguiente al cierre del barrido
(`simulate_htf_only` en `engine.py`), SL = mecha del barrido + buffer,
TP = extremo opuesto de C1. Barrido completo en
`runs/2026-09-06_mnq4h_native_htf_only_barrido.csv`.

| Buffer de SL | Muestra (train/test) | PF train/test | Winrate train/test |
|---|---|---|---|
| 0.05×ATR (SL ajustado, como pide la teoría) | 1710/586 | 0.62/0.83 | 30%/36% |
| 0.3×ATR | 1379/424 | 0.66/0.84 | 37%/43% |
| 0.5×ATR | 1145/300 | 0.75/0.94 | 45%/51% |
| 1.0×ATR (SL bien ancho) | 659/103 | 0.93/1.14 | 59%/67% |

**Con SL ajustado a la mecha (lo que pide la teoría CRT tal cual) y
muestra grande (1700+ operaciones), el resultado es un "sin edge" muy
sólido** — PF claramente por debajo de 1 en train y test, winrate de
apenas 28-36%. Esto confirma con muestra real la misma sospecha que ya
había surgido en `fvg_continuation`: un SL pegado al nivel de invalidez
(acá, la mecha del barrido) se lleva puesto la mayoría de los setups que
en realidad eran correctos, porque el precio suele extenderse un poco
más allá antes de revertir.

Alejar el SL ayuda muchísimo (winrate hasta 67%) pero la muestra se
achica al mismo ritmo, y el mejor caso (buffer 1.0×ATR) todavía tiene
train por debajo de 1 (0.93) aunque el test cruce a 1.14 — mejora clara
pero sin confirmar del todo en ambos períodos. **Es la pista más
prometedora de las tres variantes de FVG probadas en esta sesión, pero
todavía no cruza la barra de "validado".**

## Próximo paso sugerido

Si se retoma esto: probar SL bien ancho (0.75-1.5×ATR más allá de la
mecha) como línea base, en vez del ajustado que pide la teoría clásica —
y una vez encontrado un buffer que funcione en el HTF puro, recién ahí
sumarle la capa de FVG en timeframe menor (para lo cual sigue haciendo
falta más historial nativo de 15m/5m del que tenemos hoy — sólo 5
meses).

## Uso

```bash
pip install -r requirements.txt
python3 optimize.py ruta/a/tus_datos_LTF.csv --htf-rule 1h,4h --max-wait-htf-bars 1,2,3,4
```

El CSV pasado es el timeframe MENOR — el mayor se deriva por resample
adentro del motor (`data.resample_ohlc`), así que `--htf-rule` tiene que
ser múltiplo exacto del timeframe del CSV cargado. Ver `data.py` para el
formato esperado (export de TradingView, igual que el resto del repo).
