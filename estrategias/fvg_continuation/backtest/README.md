# Backtester de FVG Continuación

Réplica en Python de una estrategia de **continuación** basada en Fair
Value Gaps, hermana conceptual de `ifvg_sniper` pero con la lógica de
entrada invertida — ver docstring de `engine.py` para el detalle completo.

## Qué estrategia es esta

- **`ifvg_sniper`** (ya validado en este repo): espera a que el precio
  **rompa** el FVG (lo invierte) y opera el retest de esa zona ya
  invertida — es una estrategia de **reversión**.
- **FVG Continuación** (esta carpeta): opera el retest del FVG
  **original**, sin esperar a que se rompa — el gap actúa como
  soporte/resistencia a favor del impulso que lo creó. Es una estrategia
  de **continuación**.
- SL apenas afuera de la zona (no un múltiplo de ATR desde la entrada).
- TP en el próximo swing/liquidez sin mitigar (pivote no tocado en la
  dirección del trade), no un R:R fijo — el R:R sale de dónde está ese
  swing, y se filtra con `min_rr` como mínimo aceptable.

## Veredicto: DESCARTADA (dos enfoques de SL probados, ninguno con edge)

Se corrió un barrido de parámetros sobre MNQ 1m/3m/5m/15m (mismos datos
que `ifvg_sniper`, ver `runs/2026-09-06_mnq*_barrido_inicial.csv`) con
split train/test 70/30:

| Timeframe | Mejor PF test (sin filtrar consistencia) | Lectura |
|---|---|---|
| 1m | 0.77 | Sin edge |
| 3m | 0.86 | Sin edge |
| 5m | 0.66 | Sin edge (el peor) |
| 15m | 1.64, pero train ≈1.0 | Ruido de muestra chica (33 trades en test), no edge real |

**Ninguna combinación probada hasta ahora muestra un edge consistente
train+test en ningún timeframe.** Hipótesis de por qué: el SL "apenas
afuera de la zona" es demasiado ajustado para cómo se comporta el precio
en el reteste de un FVG recién formado — es común que el precio meta una
mecha (o rellene casi toda la zona) antes de continuar en la dirección
original, así que un SL tan pegado se lleva puesto ese barrido de
liquidez la mayoría de las veces.

**Segunda vuelta — SL más alejado (`sl_mode=impulse_candle`), también
descartado:** se agregó una variante de SL usando el extremo de la vela
de impulso completa (i-1) en vez del borde de la zona, con la misma
lógica de barrido en las 4 temporalidades (ver
`runs/2026-09-06_mnq*_sl_mode_comparativo.csv`):

| Timeframe | `impulse_candle` vs `zone` |
|---|---|
| 1m | Sin mejora — mejor caso train PF 0.47-0.50 (pésimo) con test 0.83-0.90; hueco train/test enorme, ruido |
| 3m | Empata, sin diferencia real (test PF ~0.86 en ambos) |
| 5m | **Peor** — queda uniformemente mal (train 0.81-0.84, test 0.67-0.72), mientras `zone` al menos tenía buen train |
| 15m | `zone` sigue siendo mejor en todos los casos — `impulse_candle` ni entra al top |

La distancia del SL no era el problema: alejarlo no mejora nada y en 5m
empeora. Esto apunta a que el concepto de fondo (TP en el "próximo swing
sin mitigar" para una entrada de continuación) simplemente no encuentra
objetivos alcanzables con la frecuencia necesaria en este dataset,
independientemente de dónde se ponga el stop.

**Con dos enfoques de SL distintos sin edge en ninguna de las 4
temporalidades, se descarta esta estrategia en su forma actual.** No se
armó el `.pine` — no tiene sentido pulirlo para algo sin edge validado en
Python. Si en el futuro se retoma, valdría la pena revisar el TP (no el
SL): por ejemplo, un R:R fijo por ATR en vez de buscar el swing más
cercano, o exigir un swing target más lejano/significativo en vez del
más cercano sin mitigar.

## Uso

```bash
pip install -r requirements.txt
python3 optimize.py ruta/a/tus_datos.csv --min-gap-atr 0.5,0.75,1.0 --min-rr 1.0,1.5,2.0
```

Ver `data.py` para el formato de CSV esperado (mismo que el resto del
repo: export de TradingView). Este entorno no tiene salida a internet
hacia proveedores de datos de mercado — los CSV de MNQ ya cargados acá
(gitignored, no se versionan) son los mismos que usa `ifvg_sniper`.
