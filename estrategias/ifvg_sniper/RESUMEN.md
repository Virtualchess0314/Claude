# IFVG Sniper — resumen consolidado (2026-09-05)

Instrumento: **MNQ** (Micro E-mini Nasdaq-100, CME) vía Tradeify/Tradovate.
Cuenta: **$50.000**, límite real de **40 micros**. Comisión real confirmada:
**$3.50 round-turn por contrato** (bróker + CME + NFA incluido).

## Mecánica común (todos los timeframes)

- Detección de FVG de 3 velas + filtros de forma (cuerpo ≥50% del rango,
  rango ≥0.6×ATR de la vela de impulso).
- Se invierte a IFVG cuando el precio cierra del otro lado con buffer.
- Entrada por **orden límite** en el borde roto (no a mercado) — sólo puede
  llenarse desde la vela siguiente a la confirmación, nunca con datos del
  futuro.
- Riesgo fijo en USD: el tamaño de posición se ajusta para arriesgar
  siempre `maxRiskUSD` por operación.
- `atrLen = 14` en los tres timeframes.
- Cierre forzado de sesión (no overnight) a las 16:45 ET.
- Slippage: 1 tick en salidas de mercado (SL/cierre de sesión). Comisión
  aplicada en el cálculo de todos los resultados de abajo.

## Comparación por timeframe

| | **1m** | **3m** | **5m (vigente en el .pine)** | **10m** | **15m** | **30m** |
|---|---|---|---|---|---|---|
| Historial usado | 13.800 velas, 2 semanas (23 ago–4 sep) | 11.500 velas, 1 mes (2 ago–4 sep) | 11.040 velas, 2 meses (12 jul–4 sep) — *pendiente reconstruir con más historial* | derivado del de 5m (2 meses) — *pendiente reconstruir* | 10.317 velas, 5 meses (31 mar–4 sep), export nativo | **19.796 velas, 20 meses (ene 2025–4 sep 2026), export nativo** |
| `minGapAtr` | 1.25 | 0.50 | 0.75 | 0.75 | 0.75 | 0.75 |
| `slAtrMult` | 1.00 | 1.00 | 0.75 | 0.75 | 0.75 | **1.50** |
| `rrTarget` | 1.0 | 1.0 (1.2 mejora leve, sin adoptar) | **1.5** | 1.0 | 1.5 | 1.0 |
| `cleanBreakBufferAtr` | 0.10 | 0.05 (default) | 0.10 | 0.10 | 0.05 (default) | 0.05 (default) |
| Ventana horaria de entrada | Ninguna (24h) | Ninguna (24h) | **Sólo NY 08:00–16:00 ET** | Ninguna (24h) | Ninguna (24h) | Ninguna (24h) |
| Operaciones (dataset completo) | 63 | 348 | 95 | 84 | 137 | **345** |
| Winrate | 73% | 60–66% (train/test) | 60% | 69% | 52% | 58% |
| Profit Factor | 1.82 (1.50 train/2.30 test) | 1.30 train / 1.58 test | 2.08 train / 2.29 test | 2.03 train / 1.98 test | 1.46 train / 1.35 test | 1.39 train / 2.02 test |
| Expectancy | 0.26R | 0.21R | 0.42R | 0.30R | 0.20R | 0.27R |
| Neto (dataset completo) | $4.753 | $6.707 (test) | $4.330 (test) | $1.655 (test, muestra chica: 19 trades) | $7.598 | **$15.047** |
| Drawdown máximo | -$1.462 | -$2.015 | -$1.623 | -$960 (el mejor) | -$2.169 | -$2.418 |
| **Racha máxima de SL seguidos** | 3 (-$1.033) | 6 (-$1.466) | 6 (-$1.623) | **3** (-$824, la mejor) | 7 (-$1.778) | 5 (-$705) |
| Duración promedio | **~1.5 min** | ~6.5 min | ~8.5 min | ~10.6 min | ~26 min | ~1h 45min |
| Contratos promedio (riesgo $300) | ~15 (con `minGap` más laxo llegaba a ~28, topando el límite de 40) | ~8 | ~5-7 | — | ~4 | ~1.6 |
| Volumen disponible en los datos | No | No | No (sí en un export de 6 semanas aparte) | No | No | No |
| **Veredicto** | Viable, pero sólo 2 semanas de muestra — menos confianza que el resto. Duración de ~1.5 min roza terreno de alta frecuencia | Viable | **Viable — es el que está en el .pine** | Viable (mejor consistencia/drawdown) | Viable — se revirtió el veredicto anterior al conseguir 5 meses de historial nativo | **Viable — la muestra más grande y el neto más alto de todos, con 20 meses de historial nativo** |

**Nota sobre 15m y 30m:** la primera vez que se probaron (con sólo 2 meses de
datos derivados de 5m) ninguno mostraba edge consistente. Con historial
nativo largo (5 meses para 15m, 20 meses para 30m) la conclusión cambió por
completo en los dos — el problema era la muestra insuficiente, no que el
timeframe fuera malo. **Pendiente**: reconstruir 5m y 10m con un historial
igual de largo (el usuario va a conseguir un export de 5m desde ~2024, del
que se deriva 10m) para ver si sus configs actuales también cambian con
más datos — dado el patrón de 15m/30m, es bastante probable que sí.

## Cosas probadas y su resultado

| Idea | Resultado |
|---|---|
| Ventana horaria NY (08-16 ET) | Ayuda en **5m únicamente**. Empeora en 3m, 10m, 15m, 30m. |
| Filtro de volumen (`vol_mult=0.8`) | Sólo probado en 6 semanas de MNQ 5m con volumen: sube PF (1.63→1.90) pero muestra insuficiente para validar train/test. |
| R:R más bajo (1:0.5, 1:0.75) | Peor en los tres timeframes — sube el winrate pero baja la plata neta. |
| R:R más alto (1:1.2 a 1:3) | 5m y 10m: el actual sigue siendo lo mejor. **3m: 1:1.2 es una mejora leve y consistente** ($7.120 vs $6.707); 1:2.0+ muestra señal de sobreajuste (PF de train cae a ~0.97) — no confiar en esos números pese al neto más alto. |
| Breakeven-stop (mover SL a breakeven) | **No medible con datos de 5m** — los trades duran ~1-2 velas, la ambigüedad de "qué tocó primero" es demasiado grande (rango de PF entre cotas: 0.50 a 4.42). Necesita datos de 1 min o tick. |
| Entrada más profunda (SL/TP fijos) | Empeora en 5m y 3m — filtra las operaciones más limpias (directas a TP). |
| Scale-in adverso (+1 contrato al -25% y/o -50% hacia el SL) | **Mejora real y validada train/test.** Solo 25%: +11-12% neto en test. Solo 50%: +8-9%. Combinado 25%+50%: **+20% neto en test**, PF estable, drawdown proporcionalmente mayor. Pendiente de decisión para llevarlo al `.pine`. |
| Scale-in favorable (+1 contrato al +25% hacia el TP) | Descartado — empeora PF y dispara el drawdown en los 4 escenarios probados. |
| Judas swing (ICT) | Implementado pero sin muestra suficiente para validar (1-11 operaciones en 2 meses). |
| Entrada más profunda / breakeven / Judas | Todo queda en el motor de Python para reintentar con más historial; nada de esto se llevó al `.pine`. |

## Estado actual del `.pine`

Configurado para **5m**: `minGapAtr=0.75, slAtrMult=0.75, rrTarget=1.5,
cleanBreakBufferAtr=0.10`, ventana NY 08:00–16:00 ET, `maxRiskUSD=300`,
`maxQty=40`. Alertas propias (`alert()`) agregadas para disparar ANTES del
toque del precio, no cuando la orden ya se llenó.

**Pendiente de decisión del usuario:**
1. ¿Sumar el scale-in adverso (25%+50%) al `.pine`?
2. ¿Cambiar `rrTarget` de 3m a 1.2 si se llega a operar ese timeframe?
3. Confirmar comisión/tick si se prueban otros instrumentos (GBP quedó
   pendiente por tamaño de contrato).
