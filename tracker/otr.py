"""OTR — Old Top Retest (Chirag's trigger #1: possible bottom formation).

Rules, all on the WEEKLY chart with the 5 EMA:
  1. EMA top  = a week where the 5 EMA is the highest within `swing_weeks` weeks on each side.
                Flat consolidations count too (e.g. Nifty IT Feb & Apr 2021).
  2. Real top = an EMA top higher than the EMA top before it. Lower tops are ignored.
  3. Breakout = later, both the weekly close and the 5 EMA go above that old EMA top.
  4. Untested = after the breakout, price moved well away (a weekly low >= `move_away_pct` above the
                old EMA top) and has not come back since.
  5. OTR heads-up = the FIRST day price (daily low) or the 5 EMA comes within `heads_up_pct`
                of that untested old EMA top.
     Test types:  price test = candle low reaches the zone while the 5 EMA stays above it
                  EMA test   = the 5 EMA itself comes within `test_pct` -> "deep buy"
  6. Broken = the 5 EMA later closes more than `broken_pct` below the old EMA top.
"""
from __future__ import annotations

import pandas as pd

from .indicators import ema, resample

DEFAULTS = dict(ema_length=5, swing_weeks=4, move_away_pct=10, heads_up_pct=2, test_pct=1.5, broken_pct=3)


def _cfg(cfg: dict | None) -> dict:
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return c


def weekly(daily: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    w = resample(daily.sort_values("date"), "W-FRI").reset_index(drop=True)
    w["ema"] = ema(w["close"].astype(float), n)
    return w.dropna(subset=["ema"]).reset_index(drop=True)


def ema_tops(e: pd.Series, n: int) -> list[int]:
    tops: list[int] = []
    for i in range(1, len(e) - 1):
        lo, hi = max(0, i - n), min(len(e), i + n + 1)
        if e[i] == e[lo:hi].max() and e[i] > e[i - 1]:
            if tops and i - tops[-1] <= n:
                if e[i] > e[tops[-1]]:
                    tops[-1] = i
                continue
            tops.append(i)
    return tops


def levels(daily: pd.DataFrame, cfg: dict | None = None) -> list[dict]:
    """Every real old EMA top with its OTR status, using daily candles for exact dates."""
    c = _cfg(cfg)
    d = daily.sort_values("date").reset_index(drop=True)
    w = weekly(d, int(c["ema_length"]))
    e = w["ema"]
    # weekly EMA value for every day (the EMA of the week that day belongs to)
    wk_of_day = pd.merge_asof(d[["date"]], w[["date", "ema"]].rename(columns={"date": "wk"}),
                              left_on="date", right_on="wk", direction="forward")
    d_ema = wk_of_day["ema"].fillna(e.iloc[-1]).values

    out, prev = [], None
    away, heads = 1 + c["move_away_pct"] / 100, 1 + c["heads_up_pct"] / 100
    test, broken = 1 + c["test_pct"] / 100, 1 - c["broken_pct"] / 100
    for i in ema_tops(e, int(c["swing_weeks"])):
        L = float(e[i])
        real = prev is None or L > prev
        prev = L
        if not real:
            continue
        z = {"level": round(L, 2), "top_week": w["date"][i].strftime("%Y-%m-%d"), "status": "not broken above"}
        br = next((j for j in range(i + 1, len(w)) if w["close"][j] > L and e[j] > L), None)
        if br is None:
            out.append(z)
            continue
        z["breakout_week"] = w["date"][br].strftime("%Y-%m-%d")
        armed = next((j for j in range(br, len(w)) if w["low"][j] >= L * away), None)
        if armed is None:
            z["status"] = "near breakout"
            out.append(z)
            continue
        z["armed_week"] = w["date"][armed].strftime("%Y-%m-%d")
        start = d.index[d["date"] > w["date"][armed]]
        first = next((k for k in start if d["low"][k] <= L * heads or d_ema[k] <= L * heads), None)
        if first is None:
            z["status"] = "untested"
            out.append(z)
            continue
        z["status"] = "tested"
        z["heads_up_date"] = d["date"][first].strftime("%Y-%m-%d")
        z["heads_up_low"] = round(float(d["low"][first]), 2)
        z["heads_up_ema"] = round(float(d_ema[first]), 2)
        # what happened next (weekly)
        t = int(w.index[w["date"] >= d["date"][first]][0])
        lost = next((j for j in range(t, len(w)) if e[j] < L * broken), None)
        win = range(t, min(len(w), t + 13))
        lj = min(win, key=lambda j: w["low"][j])
        z["low"] = round(float(w["low"][lj]), 2)
        z["low_week"] = w["date"][lj].strftime("%Y-%m-%d")
        z["kind"] = ("EMA test (deep buy)" if any(e[j] <= L * test for j in win) else
                     "price test" if any(w["low"][j] <= L * test for j in win) else
                     f"near miss (within {c['heads_up_pct']}%)")
        upto = lost if lost is not None else len(w)
        z["bounce_pct"] = round((w["high"][t:upto].max() / w["low"][lj] - 1) * 100, 1) if upto > t else 0.0
        if lost is not None:
            z["broken_week"] = w["date"][lost].strftime("%Y-%m-%d")
        z["state"] = ("broken" if lost is not None and lost <= t + 8 else
                      "held" if z["bounce_pct"] >= 15 else
                      "in progress" if t >= len(w) - 26 else "weak")
        out.append(z)
    return out


def heads_up_today(daily: pd.DataFrame, cfg: dict | None = None) -> list[dict]:
    last = daily["date"].max().strftime("%Y-%m-%d")
    return [z for z in levels(daily, cfg) if z.get("heads_up_date") == last]


def nearest_untested(daily: pd.DataFrame, cfg: dict | None = None) -> dict | None:
    close = float(daily.sort_values("date")["close"].iloc[-1])
    cands = [z for z in levels(daily, cfg) if z["status"] == "untested" and z["level"] < close]
    if not cands:
        return None
    z = dict(max(cands, key=lambda x: x["level"]))
    z["distance_pct"] = round((close / z["level"] - 1) * 100, 2)
    return z
