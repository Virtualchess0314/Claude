# Hull Suite (con SL/TP por ATR) — resumen de factibilidad (2026-09-06)

Instrumento: **MNQ** (Micro E-mini Nasdaq-100, CME). Datos exportados de
TradingView por el usuario:

| Timeframe | Velas | Período |
|---|---|---|
| 1m | 13.800 | 23 ago – 4 sep 2026 (~2 semanas) |
| 5m | 11.040 | 12 jul – 4 sep 2026 (~2 meses) |
| 15m | 10.317 | 31 mar – 4 sep 2026 (~5 meses) |

**Faltan 3m y 10m** (se mencionaron pero no se subieron todavía) — pendiente
si se quiere completar la comparación.

## Metodología

El `.pine` original de Hull Suite no tiene SL/TP (está siempre adentro del
mercado). Para evaluar si el cruce de la Hull MA sirve como **gatillo de
entrada** de un sistema con riesgo administrado, se usó la variante
`simulate_atr_stops()` (`backtest/engine.py`): SL = `sl_atr_mult` × ATR(14),
TP = `rr_target` × riesgo, una operación a la vez.

Para cada timeframe se barrió `length` (período de la Hull MA), `mode`
(Hma/Ehma/Thma), `sl_atr_mult` y `rr_target` (fijado a 1.0/1.5/2.0 = R:R
1:1, 1:1.5, 1:2, como se pidió), partiendo el historial en **70% train /
30% test** y rankeando por el **mínimo entre el profit factor de train y
el de test** — no por el mejor resultado de test a secas, que puede ser
sólo suerte de la muestra. Corridas completas en `backtest/runs/2026-09-06_mnq*_atr-rr_sweep.csv`.

## Resultado por timeframe

### 1m — viable, pero con matices

Mejor zona: `length` 55-89, `mode` Hma/Ehma/Thma (no depende de uno en
particular), **SL 2.5-4.0×ATR**, **R:R 1:2**. Profit factor train
1.20-1.41, test 1.21-1.68 — consistente en varias combinaciones distintas,
no un pico aislado.

- **Muestra chica**: sólo 2 semanas de historial → 47-170 operaciones en
  train, 27-81 en test. Con tan pocas operaciones, cualquiera de estos
  números puede moverse bastante con más historial.
- Un SL de 2.5-4× ATR en 1m es un stop bastante ancho para el timeframe —
  las operaciones duran en promedio 48-127 velas (**~50 min a 2 horas**),
  es decir, funciona más como un swing corto que como scalping, a pesar de
  usar datos de 1 minuto.
- **Antes de confiar en esto hace falta más historial** (idealmente varios
  meses de 1m) para separar señal real de ruido de muestra chica.

### 5m — el más convincente de los tres

Mejor zona: `length` 8-26 (**mucho más corto** que el default del
indicador, que es 55 — a 5m conviene una Hull MA más reactiva), `mode`
Hma/Ehma/Thma, **SL 1.0-1.5×ATR**, **R:R 1:1 a 1:1.5**. Profit factor train
1.10-1.16, test 1.10-1.32, con **muestras grandes** (500-700 operaciones en
train, 230-300 en test) — mucho más confiable que el resultado de 1m.

- Ejemplo destacado: `length=10, mode=Ehma, sl_atr_mult=1.25, rr_target=1.5`
  → PF 1.14 train / 1.26 test, 572 operaciones train / 253 test, retorno
  neto de test +39.8% (arriesgando 1% del equity por operación).
- **Frecuencia alta**: ~5-9 velas de duración promedio (25-45 min) y
  230-300 operaciones en ~2-3 semanas de test → **~15-20 operaciones por
  día**. Si esto se fuera a operar en una cuenta fondeada, revisar el
  límite de "alta frecuencia" de la firma antes de asumirlo viable (mismo
  tipo de advertencia que en `ifvg_sniper/backtest/README.md`).
- Todavía sin comisión ni slippage — con esa frecuencia de operación, el
  costo por operación importa mucho más que en un sistema de pocas
  operaciones grandes. Falta correr sensibilidad a comisión antes de
  confirmar esto como viable de verdad.

### 15m — sin edge

Barrido amplio (`length` 8-89, los tres modos, SL 0.5-3.0×ATR, R:R
1:1/1:1.5/1:2): **ninguna combinación** muestra profit factor consistente
por encima de 1.0 en train Y test a la vez. Los mejores casos quedan
prácticamente en el punto de equilibrio (PF 0.98-1.08), sin ninguna
tendencia clara al subir/bajar SL o R:R. **No se recomienda operar Hull
Suite a 15m** con esta lógica de entrada — al menos no con los 5 meses de
historial disponibles.

## Cómo leer esto (ninguno de los tres está "confirmado")

Estos son resultados de un solo barrido train/test, no una validación
robusta:

1. **1m y 15m** tienen poco historial relativo a lo que hace falta para
   confiar en el resultado (2 semanas y unas pocas centenas de operaciones
   como mucho). **5m** es el que tiene mejor base estadística.
2. Ninguno de los tres incluye todavía **comisión ni slippage** — el punto
   de equilibrio real de comisión (como se hizo en
   `ifvg_sniper/backtest/cost_sensitivity.py`) no se corrió acá.
3. El position sizing es un **% fijo de equity por operación** (`risk_pct`,
   default 1%), no un riesgo fijo en dólares con límite de contratos de
   MNQ — no se validó contra las reglas de una cuenta real (margen, tope
   de micros, etc.).
4. Antes de operar cualquiera de estas combinaciones en real: correr el
   Strategy Tester de TradingView con esos mismos parámetros como control
   cruzado, y sumar comisión/slippage realistas al backtest de Python.

## Próximos pasos sugeridos

- Conseguir 3m y 10m para completar la comparación entre timeframes.
- Si 5m sigue viendo bien con más historial, correr sensibilidad de
  comisión (como en `ifvg_sniper`) antes de darlo por confirmado.
- Para 1m, conseguir más de 2 semanas de historial antes de sacar
  conclusiones firmes.
