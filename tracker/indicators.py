"""Small, dependency-free indicator helpers (pandas only).

These are the building blocks alert rules can use, e.g.
    cross_above(close, sma(close, 50))
    rsi(close, 14) < 30
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def highest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).max()


def lowest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).min()


def prev(s: pd.Series, n: int = 1) -> pd.Series:
    return s.shift(n)


def cross_above(a, b) -> pd.Series:
    a, b = _series(a), _series(b, like=a)
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_below(a, b) -> pd.Series:
    a, b = _series(a), _series(b, like=a)
    return (a < b) & (a.shift(1) >= b.shift(1))


def pct_change(s: pd.Series, n: int = 1) -> pd.Series:
    return s.pct_change(n) * 100


def _series(x, like=None):
    if isinstance(x, pd.Series):
        return x
    return pd.Series(x, index=like.index if like is not None else None)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Turn daily candles into weekly ('W-FRI') or monthly ('ME') candles.
    The candle is dated on the last trading day inside the period."""
    d = df.set_index("date").sort_index()
    agg = d.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                "close": "last", "volume": "sum"})
    last_day = d["close"].resample(rule).apply(lambda x: x.index.max() if len(x) else pd.NaT)
    agg.index = last_day.values
    agg = agg.dropna(subset=["close"])
    agg.index.name = "date"
    return agg.reset_index()


FUNCS = dict(sma=sma, ema=ema, rsi=rsi, macd=macd, highest=highest, lowest=lowest, prev=prev,
             cross_above=cross_above, cross_below=cross_below, pct_change=pct_change, abs=abs)
