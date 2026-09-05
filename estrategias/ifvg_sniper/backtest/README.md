# Backtester de IFVG Sniper

Réplica en Python de la lógica exacta de `ifvg_sniper.pine`, para barrer
parámetros mucho más rápido que probando manualmente en TradingView.

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

## Limitaciones a tener en cuenta

- Si en la misma vela se tocan SL y TP, el motor asume que el SL se ejecutó
  primero (supuesto conservador). El Strategy Tester de TradingView tiene la
  misma ambigüedad salvo que actives "bar magnifier".
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
- Una vez que tengas una combinación ganadora acá, hay que llevarla de vuelta
  a los inputs del `.pine` y confirmarla corriendo el Strategy Tester (o
  Deep Backtesting) de TradingView sobre esos mismos datos, como control
  cruzado antes de operarla en real.
