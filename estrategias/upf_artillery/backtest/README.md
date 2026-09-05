# Backtester de UPF Artillery

Réplica en Python de `upf_artillery.pine` (versión ya corregida: TP
escalonado con qty entera, sin solape sesión/EOD, reset diario en la
apertura de sesión). Misma metodología que el backtester de IFVG Sniper:
partición train/test en el tiempo, ranking por resultado en test.

## Diferencias importantes respecto al motor de IFVG

- **Sin datos de volumen todavía.** Los CSV que veníamos usando (export
  de TradingView con el indicador IFVG cargado en el gráfico) no traen
  columna de volumen — solo tienen los plots de ese indicador. El motor
  lo detecta automáticamente: si no hay columna `volume`, el filtro de
  volumen se desactiva (`vol_ok` siempre `True`) y lo avisa por consola.
  Para probar el filtro de verdad hace falta re-exportar con volumen
  incluido (casilla en el diálogo de export de TradingView).
- **Tamaño de posición fijo (`base_qty`), no basado en riesgo en USD**
  como en IFVG. Tiene que ser múltiplo de 10 para que la salida en 3
  etapas (40%/50%/resto) reparta contratos enteros — con 1 solo
  contrato, esos porcentajes redondean a 0 y nunca se ejecutan.
- **`initial_capital` importa de verdad acá**, porque el límite de
  drawdown diario (`max_dd_pct`) se calcula como % de la equity al
  inicio del día de trading. Ajustalo a tu cuenta real (se cambió el
  default de 10.000 —heredado de la plantilla original del indicador—
  a 50.000, tu cuenta de Tradeify) antes de sacar conclusiones: con
  capital mal calibrado, una sola operación perdedora puede activar el
  freno diario y distorsionar todo el resultado.
- **Comisión por fill, no round-turn.** Cada entrada y cada tramo de
  salida (T1/T2/T3) paga `commission_per_contract` por separado —así es
  como Pine aplica `commission_type=cash_per_contract`. El valor
  ($0.62) se dejó igual al del `.pine` a pedido del usuario.
- **Slippage en TODOS los fills** (entrada y las tres salidas), no sólo
  en salidas "de mercado" como en IFVG — así es como Pine aplica el
  parámetro `slippage` de `strategy()`.

## Uso

```bash
pip install -r ../../ifvg_sniper/backtest/requirements.txt  # pandas + numpy

python3 optimize.py tus_datos.csv
python3 optimize.py tus_datos.csv --sd-mult 0.5,1.0,1.5 --sl-mult 1.0,1.5,2.0
```

## Qué mirar en el resultado

Mismo criterio que con IFVG: priorizar consistencia train/test sobre el
mayor profit factor aislado, desconfiar de combinaciones con pocas
operaciones en test, y mirar el drawdown máximo en dólares reales antes
de emocionarse con el profit factor.

## Limitaciones a tener en cuenta

- Misma simplificación que IFVG: si en la misma vela se tocan SL y algún
  TP, se asume que el SL se ejecuta primero (conservador).
- Los pivotes (`ta.pivothigh`/`ta.pivotlow`) se replican de forma
  vectorizada (ventana rodante) — no repintan, coinciden con el
  comportamiento real de Pine.
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
