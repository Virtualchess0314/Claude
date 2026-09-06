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

## Estado: sin edge validado todavía (barrido inicial negativo)

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

**Pendiente de decisión:** probar una variante con el SL más alejado
(ej. más allá del extremo de la vela de impulso completa, no sólo
"afuera de la zona") antes de dar esto por descartado del todo. Por eso
no se armó todavía el `.pine` — no tiene sentido pulir la implementación
en Pine Script de algo que no mostró edge en el motor de Python.

## Uso

```bash
pip install -r requirements.txt
python3 optimize.py ruta/a/tus_datos.csv --min-gap-atr 0.5,0.75,1.0 --min-rr 1.0,1.5,2.0
```

Ver `data.py` para el formato de CSV esperado (mismo que el resto del
repo: export de TradingView). Este entorno no tiene salida a internet
hacia proveedores de datos de mercado — los CSV de MNQ ya cargados acá
(gitignored, no se versionan) son los mismos que usa `ifvg_sniper`.
