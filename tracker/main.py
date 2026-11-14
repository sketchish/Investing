"""Entry point.

  python -m tracker.main daily    # weekday evenings: update ~50 indices, check daily rules
  python -m tracker.main weekly   # Saturday: check weekly rules on the finished week + weekly summary
  python -m tracker.main monthly  # 1st of month: update every sector index, monthly rules + overview
  python -m tracker.main site     # only rebuild the chart website data
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yaml

from . import fetch, notify
from .indicators import resample, rsi, sma
from .rules import applies_to, check

ROOT = fetch.ROOT
SITE_DATA = ROOT / "site" / "data"
REPORTS = ROOT / "reports"
IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://{owner}.github.io/{repo}/"


def cfg(name: str) -> list[dict]:
    return yaml.safe_load(open(ROOT / "config" / name))["indices"]


def all_items() -> list[dict]:
    seen, out = set(), []
    for it in cfg("daily.yaml") + cfg("monthly.yaml"):
        if it["name"] not in seen:
            seen.add(it["name"])
            out.append(it)
    return out


def candles(name: str) -> pd.DataFrame | None:
    p = fetch.DATA / f"{fetch.slug(name)}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    return df if len(df) else None


def source_of(name: str) -> str:
    p = fetch.DATA / f"{fetch.slug(name)}.source"
    return p.read_text().strip() if p.exists() else "-"


def frame(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "daily":
        return df
    if tf == "weekly":
        return resample(df, "W-FRI")
    m = resample(df, "ME")
    # only finished months
    first = pd.Timestamp(date.today().replace(day=1))
    return m[m["date"] < first]


def pct(a, b):
    return None if (a is None or b in (None, 0) or pd.isna(a) or pd.isna(b)) else round((a / b - 1) * 100, 2)


# ------------------------------------------------------------------ site data
def build_site() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    daily_names = {it["name"] for it in cfg("daily.yaml")}
    listing = []
    for it in all_items():
        df = candles(it["name"])
        if df is None:
            continue
        s = fetch.slug(it["name"])

        def rows(d):
            return [[x.date.strftime("%Y-%m-%d"), round(x.open, 2), round(x.high, 2), round(x.low, 2),
                     round(x.close, 2)] for x in d.itertuples()]

        w, m = resample(df, "W-FRI"), resample(df, "ME")
        cutoff = df["date"].max() - pd.Timedelta(days=365 * 3)
        json.dump({"name": it["name"], "sector": it.get("sector", it.get("group", "")),
                   "source": source_of(it["name"]), "etf": it.get("etf"),
                   "daily": rows(df[df["date"] >= cutoff]), "weekly": rows(w), "monthly": rows(m)},
                  open(SITE_DATA / f"{s}.json", "w"), separators=(",", ":"))
        c = df["close"]
        last = c.iloc[-1]
        listing.append({
            "name": it["name"], "slug": s, "sector": it.get("sector", it.get("group", "")),
            "daily_tracked": it["name"] in daily_names, "source": source_of(it["name"]),
            "etf": it.get("etf"), "last_date": df["date"].iloc[-1].strftime("%Y-%m-%d"), "close": round(last, 2),
            "chg_1d": pct(last, c.iloc[-2] if len(c) > 1 else None),
            "chg_1w": pct(last, c.iloc[-6] if len(c) > 5 else None),
            "chg_1m": pct(last, c.iloc[-22] if len(c) > 21 else None),
            "chg_1y": pct(last, c.iloc[-253] if len(c) > 252 else None),
            "rsi14": None if pd.isna(rsi(c, 14).iloc[-1]) else round(rsi(c, 14).iloc[-1], 1),
            "above_50dma": bool(last > sma(c, 50).iloc[-1]) if len(c) >= 50 else None,
            "above_200dma": bool(last > sma(c, 200).iloc[-1]) if len(c) >= 200 else None,
            "from_52w_high": pct(last, df["high"].tail(252).max()),
        })
    json.dump({"updated": datetime.now(IST).strftime("%d %b %Y %H:%M IST"), "indices": listing},
              open(SITE_DATA / "index.json", "w"), indent=0)
    print(f"Site data: {len(listing)} indices")


# ------------------------------------------------------------------ alerts
def run_rules(tf: str, items: list[dict]) -> list[dict]:
    rules = [r for r in yaml.safe_load(open(ROOT / "config" / "rules.yaml"))["rules"]
             if r.get("enabled", True) and r.get("timeframe", "daily") == tf]
    hits = []
    for it in items:
        df = candles(it["name"])
        if df is None:
            continue
        f = frame(df, tf)
        for r in rules:
            if not applies_to(r, it["name"]):
                continue
            try:
                if check(r, f):
                    last = f.iloc[-1]
                    hits.append({"rule": r["name"], "index": it["name"], "date": last["date"].strftime("%d %b %Y"),
                                 "close": round(float(last["close"]), 2), "source": source_of(it["name"]),
                                 "etf": it.get("etf")})
            except Exception as e:  # noqa: BLE001
                print(f"Rule '{r['name']}' failed on {it['name']}: {e}")
    return hits


def hits_markdown(hits: list[dict]) -> str:
    if not hits:
        return "_No alert rules fired._\n"
    out = []
    for rule in dict.fromkeys(h["rule"] for h in hits):
        out.append(f"### {rule}\n\n| Index | Candle date | Close | Data | ETF |\n|---|---|---|---|---|")
        for h in [h for h in hits if h["rule"] == rule]:
            src = "index" if h["source"] in ("nifty", "yahoo") else "ETF price"
            out.append(f"| {h['index']} | {h['date']} | {h['close']:,} | {src} | {h['etf'] or ''} |")
        out.append("")
    return "\n".join(out)


def hits_text(hits: list[dict]) -> str:
    if not hits:
        return "No alerts."
    return "\n".join(f"- {h['index']}: {h['rule']} (close {h['close']:,})" for h in hits)


def table_of_changes(items: list[dict], tf: str, periods: dict[str, int]) -> str:
    rows = []
    for it in items:
        df = candles(it["name"])
        if df is None:
            continue
        f = frame(df, tf)
        if len(f) < 2:
            continue
        c = f["close"]
        r = {"Index": it["name"], "Close": round(c.iloc[-1], 2)}
        for label, n in periods.items():
            r[label] = pct(c.iloc[-1], c.iloc[-1 - n]) if len(c) > n else None
        ma = sma(c, 10 if tf == "monthly" else 20).iloc[-1]
        r["Trend"] = "" if pd.isna(ma) else ("▲ above avg" if c.iloc[-1] > ma else "▼ below avg")
        rows.append(r)
    if not rows:
        return "_No data yet._"
    first = list(periods)[0]
    rows.sort(key=lambda r: (r[first] is None, -(r[first] or 0)))
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join("" if r[k] is None else (f"{r[k]:+.2f}%" if k in periods else str(r[k]))
                                       for k in cols) + " |")
    return "\n".join(lines)


def write_report(mode: str, md: str) -> None:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{date.today().isoformat()}-{mode}.md").write_text(md)


def site_link() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "sketchish/sector-tracker")
    owner, name = repo.split("/")
    return SITE_URL.format(owner=owner, repo=name)


# ------------------------------------------------------------------ modes
def mode_daily():
    items = cfg("daily.yaml")
    res = fetch.run(items)
    build_site()
    hits = run_rules("daily", items)
    failed = [r for r in res if r.error and not r.last]
    today = datetime.now(IST).strftime("%d %b %Y")
    md = f"## Daily sector alerts — {today}\n\n{hits_markdown(hits)}\n\nCharts: {site_link()}\n"
    if failed:
        md += "\n<details><summary>Data problems</summary>\n\n" + "\n".join(
            f"- {r.name}: {r.error}" for r in failed) + "\n</details>\n"
    write_report("daily", md)
    if hits:
        notify.send(f"Sector alerts {today}: {len(hits)} signal(s)", md, hits_text(hits) + f"\n{site_link()}",
                    ["alert", "daily"])
    else:
        print("No daily alerts.")


def mode_weekly():
    items = cfg("daily.yaml")
    build_site()
    hits = run_rules("weekly", items)
    today = datetime.now(IST).strftime("%d %b %Y")
    table = table_of_changes(items, "weekly", {"1W": 1, "4W": 4, "13W": 13, "52W": 52})
    md = (f"## Weekly sector review — week ending {today}\n\n### Alerts\n\n{hits_markdown(hits)}\n"
          f"### All tracked sectors, best week first\n\n{table}\n\n_Trend = weekly close vs 20-week average._\n\n"
          f"Charts: {site_link()}\n")
    write_report("weekly", md)
    notify.send(f"Weekly sector review {today}" + (f": {len(hits)} signal(s)" if hits else ""), md,
                hits_text(hits) + f"\nFull table: {site_link()}", ["weekly"])


def mode_monthly():
    items = cfg("monthly.yaml")
    fetch.run(items)
    build_site()
    hits = run_rules("monthly", items + cfg("daily.yaml"))
    month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%B %Y")
    table = table_of_changes(all_items(), "monthly", {"1M": 1, "3M": 3, "6M": 6, "12M": 12})
    md = (f"## Monthly all-sector review — {month}\n\n### Alerts\n\n{hits_markdown(hits)}\n"
          f"### Every tracked sector, best month first\n\n{table}\n\n"
          f"_Trend = monthly close vs 10-month average. BSE indices without an ETF have no free data feed "
          f"and are not included._\n\nCharts: {site_link()}\n")
    write_report("monthly", md)
    notify.send(f"Monthly all-sector review — {month}" + (f": {len(hits)} signal(s)" if hits else ""), md,
                hits_text(hits) + f"\nFull table: {site_link()}", ["monthly"])


if __name__ == "__main__":
    {"daily": mode_daily, "weekly": mode_weekly, "monthly": mode_monthly, "site": build_site}[sys.argv[1]]()
