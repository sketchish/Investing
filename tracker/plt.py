"""PLT — Previous Low Test (Chirag's trigger #2: second chance after an OTR bottom).

On the WEEKLY chart with the 5 EMA, using major swings (the EMA must reverse by >= swing_pct):
  1. OTR bottom  = the weekly 5 EMA low that followed an OTR test (old EMA top held).
  2. Recent high = the first major 5 EMA top of the rally after that bottom.
  3. Previous low = the nearest major 5 EMA swing low BELOW that recent high
                    (for Nifty IT 2026 it is the bottom itself, for FMCG it can be an older,
                    higher low such as Apr 2026).
  4. PLT heads-up = price pulls back to within heads_up_pct of the previous low
                    (price test first). Caution = the 5 EMA itself comes within test_pct
                    (backtest: the low then usually breaks before the bounce).
  5. Broken      = the 5 EMA closes more than broken_pct below the previous low.
"""
from __future__ import annotations

import pandas as pd

from . import otr

DEFAULTS = dict(swing_pct=5, min_gap_pct=4, heads_up_pct=2, test_pct=1.5, broken_pct=3, max_weeks=52)


def _cfg(cfg):
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return c


def major_swings(e: pd.Series, th: float):
    """(kind, index) pairs; kind 'T' top or 'B' bottom; a swing counts once the EMA reverses by th."""
    piv, hi, lo, d = [], 0, 0, None
    for i in range(len(e)):
        if e[i] > e[hi]:
            hi = i
        if e[i] < e[lo]:
            lo = i
        if d != "down" and e[i] < e[hi] * (1 - th):
            piv.append(("T", hi, i)); d, lo = "down", i      # (kind, swing week, week it became known)
        elif d != "up" and e[i] > e[lo] * (1 + th):
            piv.append(("B", lo, i)); d, hi = "up", i
    return piv


def setups(daily: pd.DataFrame, cfg: dict | None = None, otr_cfg: dict | None = None) -> list[dict]:
    c = _cfg(cfg)
    d = daily.sort_values("date").reset_index(drop=True)
    w = otr.weekly(d, int((otr_cfg or {}).get("ema_length", 5)))
    e = w["ema"]
    piv = major_swings(e, c["swing_pct"] / 100)
    heads, test, brk = 1 + c["heads_up_pct"] / 100, 1 + c["test_pct"] / 100, 1 - c["broken_pct"] / 100

    # OTR bottoms: lowest weekly EMA within 13 weeks after an OTR heads-up (level not broken)
    bottoms = set()
    for z in otr.levels(d, otr_cfg):
        if z.get("status") != "tested" or z.get("state") == "broken":
            continue
        t = int(w.index[w["date"] >= pd.Timestamp(z["heads_up_date"])][0])
        win = range(t, min(len(w), t + 13))
        bottoms.add(min(win, key=lambda j: e[j]))

    out = []
    for b in sorted(bottoms):
        # nearest major bottom in the swing list (the OTR bottom must be a major swing low)
        bs = [p for p in piv if p[0] == "B" and abs(p[1] - b) <= 2]
        if not bs:
            continue
        b = bs[0][1]
        tops = [p for p in piv if p[0] == "T" and p[1] > b]
        if not tops:
            out.append({"bottom_week": w["date"][b].strftime("%Y-%m-%d"), "bottom_ema": round(float(e[b]), 2),
                        "stage": "rallying (no recent high yet)"})
            continue
        _, h, known = tops[0]
        H = float(e[h])
        # only lows of the current cycle: after the last major top that was above the recent high
        higher_tops = [q[1] for q in piv if q[0] == "T" and q[1] < b and e[q[1]] > H]
        since = higher_tops[-1] if higher_tops else 0
        lows = [p for p in piv if p[0] == "B" and since < p[1] <= b and e[p[1]] < H * (1 - c["min_gap_pct"] / 100)]
        lows = [p for p in lows if e[p[1]] >= e[b] * 0.999]                    # at or above the OTR bottom
        if not lows:
            continue
        p = max(lows, key=lambda q: e[q[1]])                                   # nearest below the recent high
        PL = float(e[p[1]])
        s = {"bottom_week": w["date"][b].strftime("%Y-%m-%d"), "bottom_ema": round(float(e[b]), 2),
             "high_week": w["date"][h].strftime("%Y-%m-%d"), "high_ema": round(H, 2),
             "prev_low_week": w["date"][p[1]].strftime("%Y-%m-%d"), "prev_low": round(PL, 2), "stage": "waiting for pullback"}
        end = min(len(w), h + int(c["max_weeks"]))
        # first DAY price comes within heads_up_pct of the previous low, after the recent high
        dd = d[(d["date"] > w["date"][h]) & (d["date"] <= w["date"][end - 1])]
        hit = dd[dd["low"] <= PL * heads]
        if hit.empty:
            if end < len(w):
                s["stage"] = "expired (no pullback within a year)"
            out.append(s)
            continue
        k = hit.index[0]
        s["heads_up_date"] = d["date"][k].strftime("%Y-%m-%d")
        s["heads_up_low"] = round(float(d["low"][k]), 2)
        t = int(w.index[w["date"] >= d["date"][k]][0])
        win = range(t, min(len(w), t + 13))
        s["ema_test"] = any(e[j] <= PL * test for j in win)
        ct = next((j for j in win if e[j] <= PL * test), None)
        if ct is not None:
            s["caution_week"] = w["date"][ct].strftime("%Y-%m-%d")
        s["kind"] = "EMA reached low (caution)" if s["ema_test"] else "price test (strong)"
        lost = next((j for j in range(t, min(len(w), t + 26)) if e[j] < PL * brk), None)   # within 6 months
        s["low"] = round(float(min(w["low"][j] for j in win)), 2)
        s["state"] = ("broken" if lost is not None else
                      "in progress" if t >= len(w) - 26 else "held")
        if lost is not None:
            s["broken_week"] = w["date"][lost].strftime("%Y-%m-%d")
        c0 = float(w["close"][t])
        for n in (4, 13, 26):
            s[f"after_{n}w"] = round((float(w["close"][t + n]) / c0 - 1) * 100, 1) if t + n < len(w) else None
        s["stage"] = "triggered"
        out.append(s)
    return out


def heads_up_today(daily: pd.DataFrame, cfg=None, otr_cfg=None) -> list[dict]:
    last = daily["date"].max().strftime("%Y-%m-%d")
    return [s for s in setups(daily, cfg, otr_cfg) if s.get("heads_up_date") == last]


def caution_this_week(daily: pd.DataFrame, cfg=None, otr_cfg=None) -> list[dict]:
    """PLT caution: the weekly 5 EMA reached the previous low in the latest week (sent once)."""
    d = daily.sort_values("date")
    wk = otr.weekly(d, int((otr_cfg or {}).get("ema_length", 5)))["date"].iloc[-1].strftime("%Y-%m-%d")
    out = []
    for s in setups(d, cfg, otr_cfg):
        if s.get("caution_week") == wk and s.get("heads_up_date"):
            out.append(s)
    return out


def live(daily: pd.DataFrame, cfg=None, otr_cfg=None) -> list[dict]:
    """Setups that matter now: waiting for the pullback, or tested within the last 6 months."""
    return [s for s in setups(daily, cfg, otr_cfg)
            if s.get("stage") == "waiting for pullback" or s.get("state") == "in progress"]
