# Backtester de Mecha Fade

Réplica en Python de `mecha_fade.pine`. Misma metodología que
ifvg_sniper/upf_artillery: partición train/test en el tiempo, ranking
por resultado consistente entre ambos tramos, filtro de mínimo de
operaciones para no confiar en muestras chicas.

## ⚠️ Puntos abiertos — confirmar antes de confiar en cualquier resultado

Esta primera versión se armó a partir de la descripción de la
estrategia ("3 indicadores -AlphaTrend, Pivot y DIY- buscando cada que
el precio haga una mecha por fuera, entro al cierre buscando el
movimiento opuesto"), pero quedaron 3 cosas sin definir que **cambian
el resultado del backtest**:

1. **¿Qué es el indicador "DIY"?** No hay una fórmula pública estándar
   con ese nombre, así que se implementó como **placeholder**: un canal
   Donchian (máximo/mínimo de `diy_period` velas, default 20) actuando
   como soporte/resistencia. Está aislado en una sola función
   (`diy_upper_lower()` en `engine.py`, y el bloque "DIY" en el `.pine`)
   para que sea fácil de reemplazar por el indicador real (fórmula,
   código Pine, o el nombre exacto si es público) sin tocar el resto
   del motor.
2. **¿Los 3 indicadores tienen que coincidir a la vez, o alcanza con
   cualquiera?** Se implementó configurable (`confluence_need`, 1 a 3),
   default = 1 (cualquiera de los 3 dispara), porque "cada que" sugiere
   que cada indicador funciona como gatillo individual. Si la idea real
   es "los 3 alineados", correr con `--confluence-need 3`.
3. **Regla de salida (SL/TP).** No estaba especificada. Se usó el
   supuesto más natural para un fade de mecha: SL más allá del extremo
   de la mecha que disparó la señal (+ buffer en ATR, `sl_buffer_atr`)
   y TP a un múltiplo R de ese riesgo (`tp_r_mult`). Ambos son
   parámetros de barrido en `optimize.py` — si en realidad salís de otra
   forma (ej. al tocar el indicador contrario, o con trailing), avisame
   y se ajusta el motor.

Nada de esto está escondido: mientras no se confirme, tratar cualquier
resultado con el placeholder de DIY como **exploratorio**, no como
validación de la estrategia real.

## Qué hace el motor

- **AlphaTrend**: fórmula pública estándar (ratchet de `low - ATR*mult`
  en régimen alcista según RSI/MFI ≥ 50, de `high + ATR*mult` si no).
  Usa RSI por default; si el CSV trae volumen y se activa
  `at_use_volume`, usa MFI en su lugar (igual al toggle "non-crypto
  pairs" del indicador original).
- **Pivot**: pivotes altos/bajos no repintables (`ta.pivothigh`/`low`),
  igual criterio que UPF Artillery — el último pivot alto/bajo
  confirmado actúa como resistencia/soporte.
- **DIY**: placeholder Donchian (ver arriba).
- **Señal**: para cada nivel, "mecha por fuera + cierre adentro" ->
  soporte roto momentáneamente = long, resistencia rota
  momentáneamente = short. Si en la misma vela hay señal long Y short
  (indicadores contradictorios), se descarta -no hay forma de saber
  cuál "ganaría" intrabar con datos OHLC.
- **Entrada**: a mercado en el cierre de la vela de señal (con
  slippage). **Salida**: SL (stop, con slippage) o TP (límite, sin
  slippage), lo que se toque primero -si empatan en la misma vela, se
  asume que el SL se ejecuta primero (conservador).
- **Tamaño de posición**: ajustado para arriesgar siempre
  `max_risk_usd` (igual criterio que ifvg_sniper), tope en `max_qty`
  contratos.
- **Sesión**: cierre forzado a `session_close_hour:session_close_minute`
  hora de Nueva York (desactivable con `--no-session-close` para
  FX/cripto 24h), tope opcional de operaciones/día.
- **Costos**: comisión round-turn por contrato ($3.50 default,
  confirmado Tradovate/Tradeify en los otros dos proyectos) + slippage
  en ticks.

## Uso

```bash
pip install -r requirements.txt

python3 optimize.py tus_datos.csv
python3 optimize.py tus_datos.csv --tp-r-mult 1.0,1.5,2.0 --confluence-need 1,2,3 --sl-buffer-atr 0.05,0.10,0.20
```

El CSV es el export de TradingView ("Export chart data") con al menos
`time,open,high,low,close` — `volume` es opcional.

## Qué mirar en el resultado

Mismo criterio que los otros dos backtesters del repo: priorizar
consistencia train/test sobre el mayor profit factor aislado,
desconfiar de combinaciones con pocas operaciones en test, y mirar el
drawdown máximo en dólares reales antes de emocionarse con el profit
factor. Además, acá en particular: correr por separado con
`confluence_need=1` vs `2` vs `3` para ver si la confluencia total
realmente mejora la calidad de las señales o sólo reduce el volumen sin
subir el profit factor.

## Limitaciones a tener en cuenta

- Todo lo de la sección "Puntos abiertos" arriba.
- Si en la misma vela se tocan SL y TP, se asume que el SL se ejecuta
  primero (supuesto conservador, igual que en ifvg_sniper/upf_artillery).
- El cierre por fin de sesión se aproxima al precio de cierre de esa vela.
- Los pivotes se replican de forma vectorizada (ventana rodante) — no
  repintan, coinciden con el comportamiento real de Pine.
