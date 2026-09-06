"""
Carga y resample de datos históricos OHLC para el backtester de CRT+FVG.

load_csv: igual que en el resto del repo, formato de export de TradingView.
resample_ohlc: agrupa velas más chicas en velas más grandes (ej. 15m -> 1h
agrupando de a 4). Sólo válido cuando el timeframe pedido es múltiplo
exacto del original.
"""

from __future__ import annotations

import pandas as pd


def load_csv(path: str, tz: str = "UTC") -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    time_col = next((c for c in ("time", "date", "datetime", "timestamp") if c in df.columns), None)
    if time_col is None:
        raise ValueError(
            f"No encontré columna de tiempo (time/date/datetime/timestamp) en {path}. "
            f"Columnas disponibles: {list(df.columns)}"
        )

    if pd.api.types.is_numeric_dtype(df[time_col]):
        idx = pd.to_datetime(df[time_col], unit="s", utc=True)
    else:
        idx = pd.to_datetime(df[time_col], utc=True)

    df = df.set_index(idx).sort_index()

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas {missing} en {path}. Columnas: {list(df.columns)}")

    df = df[required].astype(float)
    if tz != "UTC":
        df.index = df.index.tz_convert(tz)
    return df


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return out.dropna(subset=["open", "high", "low", "close"])
