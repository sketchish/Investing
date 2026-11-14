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
from . import otr, plt

ROOT = fetch.ROOT
SITE_DATA = ROOT / "site" / "data"
REPORTS = ROOT / "reports"
IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://{owner}.github.io/{repo}/"


def cfg(name: str) -> list[dict]:
    return yaml.safe_load(open(ROOT / "config" / name))["indices"]


def all_items() -> list[dict]:
    """Daily list first, then monthly-only indices (same index never listed twice)."""
    seen, out = set(), []
    for it in cfg("daily.yaml") + cfg("monthly.yaml"):
        key = it.get("nifty") or it.get("etf") or it["name"]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def monthly_only() -> list[dict]:
    daily = {it["name"] for it in cfg("daily.yaml")}
    return [it for it in all_items() if it["name"] not in daily]


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



def watch() -> dict:
    return yaml.safe_load(open(ROOT / "config" / "watch.yaml"))


def ma_expr(tf: str, length: int, w: dict | None = None) -> tuple[str, str]:
    """Return (formula, label) for the configured average of this length."""
    w = w or watch()
    for m in w["moving_averages"].get(tf, []):
        if int(m["length"]) == int(length):
            kind = m.get("type", "SMA").upper()
            break
    else:
        kind = "SMA"
    unit = {"daily": "day", "weekly": "week", "monthly": "month"}[tf]
    return f"{kind.lower()}(close, {int(length)})", f"{length}-{unit} {kind}"


def ma_rules(tf: str) -> list[dict]:
    w = watch()
    cfg_tf = (w.get("ma_alerts") or {}).get(tf) or {}
    out = []
    for n in cfg_tf.get("price_cross", []) or []:
        e, lab = ma_expr(tf, n, w)
        out.append({"name": f"Close crossed ABOVE the {lab}", "when": f"cross_above(close, {e})"})
        out.append({"name": f"Close crossed BELOW the {lab}", "when": f"cross_below(close, {e})"})
    for fast, slow in cfg_tf.get("ma_cross", []) or []:
        ef, lf = ma_expr(tf, fast, w)
        es, ls = ma_expr(tf, slow, w)
        out.append({"name": f"{lf} crossed ABOVE {ls}", "when": f"cross_above({ef}, {es})"})
        out.append({"name": f"{lf} crossed BELOW {ls}", "when": f"cross_below({ef}, {es})"})
    near = cfg_tf.get("near_ma")
    if near:
        k = 1 + float(near.get("within_pct", 1.0)) / 100
        for n in near.get("lengths", []):
            e, lab = ma_expr(tf, n, w)
            out.append({"name": f"Pulled back to within {near.get('within_pct', 1.0)}% of the {lab}",
                        "when": f"close > {e} and close <= {e} * {k} and prev(close) > prev({e}) * {k}"})
    scope = None
    if w.get("alert_scope", "watchlist") == "watchlist" and w.get("watchlist"):
        scope = list(w["watchlist"])
    for r in out:
        r["timeframe"] = tf
        r["indices"] = scope or "all"
    return out


QUADS = {(True, True): "Leading", (True, False): "Weakening", (False, False): "Lagging", (False, True): "Improving"}


def strength(df: pd.DataFrame, bench: pd.DataFrame | None, shift: int = 0) -> dict:
    """Relative strength vs Nifty 50: % out/under-performance and rotation zone."""
    if bench is None:
        return {}
    a = df.set_index("date")["close"]
    b = bench.set_index("date")["close"]
    rs = (a / b).dropna()
    if shift:
        rs = rs.iloc[:-shift]
    if len(rs) < 70:
        return {}
    def ch(n):
        return round((rs.iloc[-1] / rs.iloc[-1 - n] - 1) * 100, 2)
    out = {"rs_1w": ch(5), "rs_1m": ch(21), "rs_3m": ch(63)}
    out["zone"] = QUADS[(out["rs_3m"] > 0, out["rs_1m"] > 0)]
    return out



def otr_near(df: pd.DataFrame, wcfg: dict) -> dict:
    """For the Today screen: the old weekly 5 EMA top this sector is near, if any."""
    rc = wcfg.get("otr") or {}
    if not rc.get("enabled") or len(df) < 200:
        return {}
    within = float(rc.get("today_within_pct", 6))
    close = float(df["close"].iloc[-1])
    best = None
    for z in otr.levels(df, rc):
        dist = (close / z["level"] - 1) * 100
        if z["status"] == "untested" and 0 <= dist <= within:
            cand = {"level": z["level"], "since": z["top_week"][:7], "dist": round(dist, 2), "state": "untested"}
        elif z.get("state") == "in progress" and abs(dist) <= within:
            cand = {"level": z["level"], "since": z["top_week"][:7], "dist": round(dist, 2), "state": "testing",
                    "kind": z["kind"], "heads_up": z["heads_up_date"], "low": z["low"], "bounce": z["bounce_pct"]}
        else:
            continue
        if best is None or abs(cand["dist"]) < abs(best["dist"]):
            best = cand
    return {"otr": best} if best else {}


def plt_near(df: pd.DataFrame, wcfg: dict) -> dict:
    """For the Today screen: the PLT previous low this sector is near, if any."""
    pc, rc = wcfg.get("plt") or {}, wcfg.get("otr") or {}
    if not pc.get("enabled") or len(df) < 200:
        return {}
    within = float(pc.get("today_within_pct", 6))
    close = float(df["close"].iloc[-1])
    best = None
    recent = (pd.Timestamp(df["date"].max()) - pd.Timedelta(weeks=13)).strftime("%Y-%m-%d")
    for s in plt.live(df, pc, rc):
        dist = (close / s["prev_low"] - 1) * 100
        if s.get("heads_up_date"):
            if s["heads_up_date"] < recent:          # tested more than 3 months ago: no longer "today"
                continue
        elif dist > within:
            continue
        cand = {"level": s["prev_low"], "since": s["prev_low_week"][:7], "high": s["high_ema"], "dist": round(dist, 2),
                "state": "testing" if s.get("heads_up_date") else "waiting",
                "kind": s.get("kind"), "heads_up": s.get("heads_up_date"), "low": s.get("low")}
        if best is None or abs(cand["dist"]) < abs(best["dist"]):
            best = cand
    return {"plt": best} if best else {}


def ma_state(df: pd.DataFrame, w: dict) -> dict:
    """Is the last finished candle above the first configured average, and did it just cross?"""
    from .indicators import ema as _ema
    out = {}
    for tf in ("daily", "weekly"):
        mas = w["moving_averages"].get(tf) or []
        if not mas:
            continue
        m = mas[0]
        f = frame(df, tf)
        if tf == "weekly" and len(f) and f["date"].iloc[-1].weekday() != 4 and datetime.now(IST).weekday() < 5:
            f = f.iloc[:-1]          # this week's candle is not finished yet
        c = f["close"].astype(float)
        if len(c) < int(m["length"]) + 2:
            continue
        line = _ema(c, int(m["length"])) if m.get("type", "SMA").upper() == "EMA" else sma(c, int(m["length"]))
        above, was = bool(c.iloc[-1] > line.iloc[-1]), bool(c.iloc[-2] > line.iloc[-2])
        out[f"{tf[0]}_above"] = above
        out[f"{tf[0]}_cross"] = "up" if above and not was else ("down" if was and not above else None)
    return out

# ------------------------------------------------------------------ site data
def build_site() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    daily_names = {it["name"] for it in cfg("daily.yaml")}
    bench_item = next((it for it in cfg("daily.yaml") if it.get("benchmark")), None)
    bench = candles(bench_item["name"]) if bench_item else None
    wcfg = watch()
    json.dump({"moving_averages": wcfg["moving_averages"], "rsi": wcfg.get("rsi", {"length": 14}),
               "watchlist": wcfg.get("watchlist") or []}, open(SITE_DATA / "settings.json", "w"))
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
        zs = otr.levels(df, wcfg.get("otr") or {}) if ((wcfg.get("otr") or {}).get("enabled") and len(df) >= 200) else []
        pz = (plt.setups(df, wcfg.get("plt") or {}, wcfg.get("otr") or {})
              if ((wcfg.get("plt") or {}).get("enabled") and len(df) >= 200) else [])
        json.dump({"name": it["name"], "sector": it.get("sector", it.get("group", "")), "zones": zs, "plt": pz,
                   "source": source_of(it["name"]), "etf": it.get("etf"),
                   "daily": rows(df), "weekly": rows(w), "monthly": rows(m)},
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
            "benchmark": bool(it.get("benchmark")),
            **ma_state(df, wcfg),
            **otr_near(df, wcfg),
            **plt_near(df, wcfg),
            **({} if it.get("benchmark") else strength(df, bench)),
        })
    json.dump({"updated": datetime.now(IST).strftime("%d %b %Y %H:%M IST"), "indices": listing},
              open(SITE_DATA / "index.json", "w"), indent=0)
    print(f"Site data: {len(listing)} indices")


# ------------------------------------------------------------------ alerts
def run_rules(tf: str, items: list[dict]) -> list[dict]:
    rules = [r for r in yaml.safe_load(open(ROOT / "config" / "rules.yaml"))["rules"]
             if r.get("enabled", True) and r.get("timeframe", "daily") == tf] + ma_rules(tf)
    hits = []
    for it in items:
        if it.get("benchmark"):
            continue
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


def otr_cfg() -> dict:
    return watch().get("otr") or {}


def otr_hits(items: list[dict]) -> list[dict]:
    rc = otr_cfg()
    if not rc.get("enabled"):
        return []
    hits = []
    for it in items:
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        for z in otr.heads_up_today(df, rc):
            last = df.iloc[-1]
            hits.append({"rule": f"OTR heads-up: within {rc.get('heads_up_pct', 2)}% of an UNTESTED old EMA top (possible bottom)",
                         "index": it["name"], "date": last["date"].strftime("%d %b %Y"),
                         "close": round(float(last["close"]), 2), "source": source_of(it["name"]), "etf": it.get("etf"),
                         "detail": f"old weekly 5 EMA top {z['level']:,.2f} (week of {z['top_week']}); "
                                   f"today's low {z['heads_up_low']:,.2f}, weekly 5 EMA {z['heads_up_ema']:,.2f}"})
    return hits


def plt_cfg() -> dict:
    return watch().get("plt") or {}


PLT_NAME = "PLT — Previous Low Test"


def plt_hits(items: list[dict]) -> list[dict]:
    pc, rc = plt_cfg(), otr_cfg()
    if not pc.get("enabled"):
        return []
    hits = []
    for it in items:
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        last = df.iloc[-1]
        base = {"index": it["name"], "date": last["date"].strftime("%d %b %Y"), "close": round(float(last["close"]), 2),
                "source": source_of(it["name"]), "etf": it.get("etf")}
        for s in plt.heads_up_today(df, pc, rc):
            hits.append({**base, "rule": f"{PLT_NAME} heads-up: price within {pc.get('heads_up_pct', 2)}% of the previous low",
                         "detail": f"previous low (weekly 5 EMA) {s['prev_low']:,.2f} ({s['prev_low_week'][:7]}); "
                                   f"recent high {s['high_ema']:,.2f} ({s['high_week'][:7]}); OTR bottom {s['bottom_ema']:,.2f} "
                                   f"({s['bottom_week'][:7]}); today's low {s['heads_up_low']:,.2f}"})
        if datetime.now(IST).weekday() == 4:      # weekly candle closes on Friday
            for s in plt.caution_this_week(df, pc, rc):
                hits.append({**base, "rule": f"{PLT_NAME} caution: weekly 5 EMA reached the previous low (it may break before it bounces)",
                             "detail": f"previous low {s['prev_low']:,.2f} ({s['prev_low_week'][:7]}); heads-up was {s['heads_up_date']}"})
    return hits


def plt_markdown(items: list[dict]) -> str:
    pc, rc = plt_cfg(), otr_cfg()
    within = float(pc.get("today_within_pct", 6))
    live, wait = [], []
    for it in items:
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        close = float(df["close"].iloc[-1])
        for s in plt.live(df, pc, rc):
            dist = (close / s["prev_low"] - 1) * 100
            if s.get("heads_up_date"):
                live.append(f"| {it['name']} | {s['prev_low']:,.2f} ({s['prev_low_week'][:7]}) | {s['heads_up_date']} | "
                            f"{s['low']:,.2f} | {s['kind']} | {dist:+.1f}% |")
            elif dist <= within:
                wait.append((dist, f"| {it['name']} | {close:,.2f} | {s['prev_low']:,.2f} ({s['prev_low_week'][:7]}) | "
                                   f"{s['high_ema']:,.2f} ({s['high_week'][:7]}) | {dist:+.1f}% |"))
    out = "**PLT tests in progress** (pulled back to the previous low in the last 6 months, low holding)\n\n"
    out += ("| Sector | Previous low | Heads-up | Low | Type | Price vs low now |\n|---|---|---|---|---|---|\n" + "\n".join(live)) if live else "_None._"
    out += f"\n\n**Getting close** (within {within}% of a previous low after an OTR bottom and first rally)\n\n"
    out += ("| Sector | Close | Previous low | Recent high | Price above it |\n|---|---|---|---|---|\n" + "\n".join(r for _, r in sorted(wait))) if wait else "_None._"
    out += "\n\n_Price test = strong (held 20 of 22 times since 2016). If the weekly 5 EMA also sinks to the low, it usually breaks first._\n"
    return out


def otr_markdown(items: list[dict], within: float = 6.0) -> str:
    rc = otr_cfg()
    near, live = [], []
    for it in items:
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        for z in otr.levels(df, rc):
            if z.get("state") == "in progress":
                live.append(f"| {it['name']} | {z['level']:,.2f} ({z['top_week'][:7]}) | {z['heads_up_date']} | "
                            f"{z['low']:,.2f} | {z['kind']} | +{z['bounce_pct']}% |")
        n = otr.nearest_untested(df, rc)
        if n and n["distance_pct"] <= within:
            near.append((n["distance_pct"], f"| {it['name']} | {df['close'].iloc[-1]:,.2f} | {n['level']:,.2f} ({n['top_week'][:7]}) | {n['distance_pct']:+.2f}% |"))
    out = "**OTR tests in progress** (came back to an old EMA top in the last 6 months, level holding)\n\n"
    out += ("| Sector | Old EMA top | Heads-up | Low | Type | Bounce so far |\n|---|---|---|---|---|---|\n" + "\n".join(live)) if live else "_None._"
    out += f"\n\n**Getting close** (within {within}% of an untested old EMA top)\n\n"
    out += ("| Sector | Close | Untested old EMA top | Price above it |\n|---|---|---|---|\n" + "\n".join(r for _, r in sorted(near))) if near else "_None._"
    return out + "\n"


def hits_markdown(hits: list[dict]) -> str:
    if not hits:
        return "_No alert rules fired._\n"
    out = []
    for rule in dict.fromkeys(h["rule"] for h in hits):
        out.append(f"### {rule}\n\n| Index | Candle date | Close | Data | ETF |\n|---|---|---|---|---|")
        for h in [h for h in hits if h["rule"] == rule]:
            src = "index" if h["source"] in ("nifty", "yahoo") else "ETF price"
            out.append(f"| {h['index']} | {h['date']} | {h['close']:,} | {src} | {h['etf'] or ''} |")
            if h.get("detail"):
                out.append(f"| ↳ {h['detail']} | | | | |")
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



def rotation_markdown(items: list[dict]) -> str:
    bench_item = next((it for it in items if it.get("benchmark")), None)
    bench = candles(bench_item["name"]) if bench_item else None
    if bench is None:
        return "_Benchmark data missing._\n"
    now, moved = {z: [] for z in QUADS.values()}, []
    for it in items:
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None:
            continue
        s, s0 = strength(df, bench), strength(df, bench, shift=5)
        if not s:
            continue
        now[s["zone"]].append(f"{it['name']} ({s['rs_1m']:+.1f}% vs Nifty, 1M)")
        if s0 and s0["zone"] != s["zone"]:
            moved.append(f"- **{it['name']}**: {s0['zone']} → **{s['zone']}**")
    out = []
    if moved:
        out.append("**Moved zone this week**\n\n" + "\n".join(moved) + "\n")
    for z, hint in [("Improving", "watch for new opportunities"), ("Leading", "strong and getting stronger"),
                    ("Weakening", "strong but losing pace"), ("Lagging", "weak")]:
        out.append(f"**{z}** — {hint}: " + (", ".join(now[z]) if now[z] else "none"))
    return "\n\n".join(out) + "\n\n_Zone = 3-month trend × 1-month momentum of the sector's strength vs Nifty 50._\n"

def write_report(mode: str, md: str) -> None:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{date.today().isoformat()}-{mode}.md").write_text(md)


def site_link() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "sketchish/Investing")
    owner, name = repo.split("/")
    return SITE_URL.format(owner=owner, repo=name)


# ------------------------------------------------------------------ modes
def mode_daily():
    items = cfg("daily.yaml")
    res = fetch.run(items)
    build_site()
    hits = run_rules("daily", items) + otr_hits(items) + plt_hits(items)
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
    table = table_of_changes([i for i in items if not i.get("benchmark")], "weekly",
                             {"1W": 1, "4W": 4, "13W": 13, "52W": 52})
    md = (f"## Weekly sector review — week ending {today}\n\n### Alerts\n\n{hits_markdown(hits)}\n"
          f"### OTR — Old Top Retest (possible bottoms)\n\n{otr_markdown(items)}\n"
          f"### {PLT_NAME} (second chance after an OTR bottom)\n\n{plt_markdown(items)}\n"
          f"### Rotation vs Nifty 50\n\n{rotation_markdown(items)}\n"
          f"### All tracked sectors, best week first\n\n{table}\n\n_Trend = weekly close vs 20-week average._\n\n"
          f"Charts: {site_link()}\n")
    write_report("weekly", md)
    notify.send(f"Weekly sector review {today}" + (f": {len(hits)} signal(s)" if hits else ""), md,
                hits_text(hits) + f"\nFull table: {site_link()}", ["weekly"])


def mode_monthly():
    fetch.run(monthly_only())   # the daily ones are already fresh
    build_site()
    hits = run_rules("monthly", all_items())
    month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%B %Y")
    table = table_of_changes(all_items(), "monthly", {"1M": 1, "3M": 3, "6M": 6, "12M": 12})
    md = (f"## Monthly all-sector review — {month}\n\n### Alerts\n\n{hits_markdown(hits)}\n"
          f"### Every tracked sector, best month first\n\n{table}\n\n"
          f"_Trend = monthly close vs 10-month average. BSE indices without an ETF have no free data feed "
          f"and are not included._\n\nCharts: {site_link()}\n")
    write_report("monthly", md)
    notify.send(f"Monthly all-sector review — {month}" + (f": {len(hits)} signal(s)" if hits else ""), md,
                hits_text(hits) + f"\nFull table: {site_link()}", ["monthly"])


def mode_otr_test():
    """One-off: email every OTR heads-up of the last 6 months, to check the alert end to end."""
    build_site()
    rc, since = otr_cfg(), (date.today() - timedelta(days=183)).isoformat()
    hits = []
    for it in cfg("daily.yaml"):
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        for z in otr.levels(df, rc):
            if z.get("heads_up_date", "") >= since:
                hits.append({"rule": "OTR heads-up (replayed)", "index": it["name"],
                             "date": pd.Timestamp(z["heads_up_date"]).strftime("%d %b %Y"),
                             "close": z["heads_up_low"], "source": source_of(it["name"]), "etf": it.get("etf"),
                             "detail": f"old 5 EMA top {z['level']:,.2f} ({z['top_week'][:7]}) · {z['kind']} · "
                                       f"low {z['low']:,.2f} · bounce +{z['bounce_pct']}% · {z['state']}"})
    hits.sort(key=lambda h: pd.Timestamp(h["date"]), reverse=True)
    md = ("## TEST — OTR heads-ups of the last 6 months\n\nThis is a one-off test. Each row is a day the OTR "
          "heads-up would have been sent. The 'Close' column shows that day's low.\n\n" + hits_markdown(hits)
          + f"\n### Today's OTR picture\n\n{otr_markdown(cfg('daily.yaml'))}\nCharts: {site_link()}\n")
    write_report("otr-test", md)
    notify.send(f"TEST — OTR heads-ups, last 6 months ({len(hits)})", md, hits_text(hits) + f"\n{site_link()}", ["otr", "test"])


def mode_plt_test():
    """One-off: email every PLT heads-up of the last 6 months, to check the alert end to end."""
    build_site()
    pc, rc, since = plt_cfg(), otr_cfg(), (date.today() - timedelta(days=183)).isoformat()
    hits = []
    for it in all_items():
        if it.get("benchmark"):
            continue
        df = candles(it["name"])
        if df is None or len(df) < 200:
            continue
        for s in plt.setups(df, pc, rc):
            if s.get("heads_up_date", "") >= since:
                hits.append({"rule": f"{PLT_NAME} heads-up (replayed)", "index": it["name"],
                             "date": pd.Timestamp(s["heads_up_date"]).strftime("%d %b %Y"),
                             "close": s["heads_up_low"], "source": source_of(it["name"]), "etf": it.get("etf"),
                             "detail": f"previous low {s['prev_low']:,.2f} ({s['prev_low_week'][:7]}) · recent high "
                                       f"{s['high_ema']:,.2f} ({s['high_week'][:7]}) · {s['kind']} · low {s['low']:,.2f} · {s['state']}"})
    hits.sort(key=lambda h: pd.Timestamp(h["date"]), reverse=True)
    md = (f"## TEST — {PLT_NAME} heads-ups of the last 6 months\n\nThis is a one-off test. Each row is a day the PLT "
          "heads-up would have been sent. The 'Close' column shows that day's low.\n\n" + hits_markdown(hits)
          + f"\n### Today's PLT picture\n\n{plt_markdown(all_items())}\nCharts: {site_link()}\n")
    write_report("plt-test", md)
    notify.send(f"TEST — PLT (Previous Low Test) heads-ups, last 6 months ({len(hits)})", md,
                hits_text(hits) + f"\n{site_link()}", ["plt", "test"])


def mode_site():
    """Refresh candles (every index) and the website only — no alert emails."""
    fetch.run(all_items())
    build_site()


if __name__ == "__main__":
    {"daily": mode_daily, "weekly": mode_weekly, "monthly": mode_monthly, "site": mode_site, "otr-test": mode_otr_test,
     "plt-test": mode_plt_test}[sys.argv[1]]()
