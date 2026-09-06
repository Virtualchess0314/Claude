# Backtester de Hull Suite

Réplica en Python de la lógica del `hull_suite.pine`, para poder barrer el
período (`length`) y la variante de Hull MA (Hma/Ehma/Thma) mucho más rápido
que probando manualmente en TradingView.

## Qué estrategia es esta

Hull Suite es un script público (indicador de InSilico, convertido a
`strategy` por DashTrader) que ya estaba armado antes de subirlo a este
repo — a diferencia de `ifvg_sniper` y `upf_artillery`, acá no hay un
motor propio pensado desde cero, es la réplica de un sistema de terceros.
Su lógica es muy simple:

- Calcula una Hull MA de `length` períodos sobre el cierre.
- Si el valor actual es mayor que el de 2 velas atrás → long. Si es menor →
  short. **Siempre está adentro del mercado** (stop-and-reverse), no hay
  SL/TP ni gestión de riesgo — el tamaño de posición es 100% del equity.
- El input `Strategy Direction` (`long`/`short`/`all`) tiene un
  comportamiento particular en el `.pine`: en modo `long` o `short`, las
  órdenes del lado contrario quedan BLOQUEADAS por completo (no es que
  cierre la posición y se quede afuera). Es decir, una vez que entra a
  favor por primera vez, la posición **nunca más se toca**, sin importar
  cuántas señales contrarias aparezcan después. El motor de Python replica
  este comportamiento tal cual (ver docstring de `engine.py`), no es un
  bug del backtester.

## Por qué esto no trae datos incluidos

Este backtester corre en un entorno sin salida a internet hacia proveedores
de datos de mercado (Yahoo Finance, Binance, etc. están bloqueados por
política de red). No hay forma de descargar velas desde acá.

**Vos tenés que exportar el historial vos mismo desde TradingView:**

1. Abrí el gráfico del instrumento/timeframe que quieras probar.
2. Cargá todo el historial posible (scrolleá hacia atrás para que
   TradingView lo vaya cargando, según tu plan).
3. Ícono de cámara/exportar en la barra de herramientas del gráfico →
   **"Export chart data"** → descargá el CSV.
4. Subí ese CSV a este repo (por ejemplo a
   `estrategias/hull_suite/backtest/data/`) o pegámelo/mandámelo para que
   lo suba yo.

El loader (`data.py`) acepta el formato tal cual lo exporta TradingView
(columna `time` en unix timestamp + `open,high,low,close`).

## Uso

```bash
pip install -r requirements.txt

# Barrido con los rangos por defecto (length: 20,55,100,180,200 · mode: Hma,Ehma,Thma)
python3 optimize.py ruta/a/tus_datos.csv

# Barrido custom
python3 optimize.py ruta/a/tus_datos.csv \
    --length 34,55,89,144,180,200 \
    --mode Hma,Thma \
    --direction all \
    --commission-pct 0.05 \
    --min-trades 15
```

Esto reparte el historial en **70% train / 30% test** (en el tiempo, nunca
mezclado al azar) y ordena el ranking por el resultado en **test**, no en
train — una combinación que sólo funciona en el pasado que ya "vio" no
sirve para operar mañana. Los resultados completos quedan en
`optimize_results.csv`.

Para correr una sola combinación puntual (por ejemplo para inspeccionar
los trades uno por uno):

```python
from data import load_csv
from engine import Params, simulate

df = load_csv("tus_datos.csv")
trades, summary = simulate(df, Params(length=55, mode="Hma", direction="all"))
print(summary)
print(trades.tail(20))
```

## Qué mirar en el resultado, en orden de importancia

1. **`test_trades`** — si hay pocas operaciones en test, cualquier métrica
   de esa fila es ruido. Con `direction=all` cada cruce es una operación
   nueva, así que longitudes (`length`) cortas dan muchas más operaciones
   que las largas (180-200, pensadas por el autor original como soporte/
   resistencia flotante, no para swing entry).
2. **`test_profit_factor` y `train_profit_factor` parecidos** — si train
   da PF 3.0 y test da PF 0.8, esa combinación está sobreajustada al
   pasado. Buscá combinaciones donde ambos números sean razonablemente
   consistentes.
3. **`test_buy_hold_pct` vs `test_net_return_pct`** — al ser un sistema
   siempre-adentro-del-mercado, la comparación obligada es contra
   comprar-y-mantener el mismo período. Un PF > 1 con un retorno neto por
   debajo del buy & hold no es necesariamente un edge real.
4. **`test_max_drawdown_pct`** — en modo `all` la estrategia siempre tiene
   el 100% del equity expuesto (long o short), así que el drawdown puede
   ser grande incluso con PF razonable.

## Limitaciones a tener en cuenta

- El fill se simula a **cierre de la vela siguiente a la señal**
  (close-to-close), no al open de esa vela como haría de verdad
  TradingView. Ver docstring de `engine.py` para el detalle — la
  diferencia práctica es chica salvo con gaps grandes entre cierre y
  apertura (crypto 24/7 no debería verse afectado; acciones/futuros con
  gaps de apertura, sí un poco).
- Las longitudes fraccionarias que produce el Pine original al dividir
  `length` (ej. `length/2`) se truncan hacia cero, igual que la conversión
  implícita float→int de Pine al pasar esos valores a `ta.wma`/`ta.ema`.
  Con `length` chico (<10) esto puede desviarse un poco del resultado
  exacto de TradingView.
- No hay comisión por defecto ni slippage modelado más allá de
  `--commission-pct` (aplicado por orden, igual que
  `commission_type=percent` del `.pine`).
- Una vez que tengas una combinación ganadora acá, hay que llevarla de
  vuelta a los inputs del `.pine` y confirmarla corriendo el Strategy
  Tester (o Deep Backtesting) de TradingView sobre esos mismos datos, como
  control cruzado antes de operarla en real.
