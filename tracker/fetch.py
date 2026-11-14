"""Download daily OHLC candles for each index and keep them in data/daily/<slug>.csv.

Sources, tried in order for each index:
  1. nifty  - official NSE index history (niftyindices.com)
  2. yahoo  - Yahoo Finance index ticker (backup)
  3. etf    - the ETF's candles on NSE via Yahoo (SYMBOL.NS)

Files are updated incrementally: only the last few weeks are re-downloaded once history exists.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "daily"
HISTORY_YEARS = 5          # how far back to go the first time
REFRESH_DAYS = 30          # how much to re-download on later runs
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
COLS = ["date", "open", "high", "low", "close", "volume"]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---------------------------------------------------------------- niftyindices
class NiftyIndices:
    URL = "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01",
                               "Origin": "https://www.niftyindices.com",
                               "Referer": "https://www.niftyindices.com/reports/historical-data",
                               "X-Requested-With": "XMLHttpRequest"})
        self.ok = True
        try:
            self.s.get("https://www.niftyindices.com/reports/historical-data", timeout=30)
        except Exception as e:  # site unreachable from this machine
            print(f"  niftyindices: home page failed ({e})")

    def _chunk(self, name: str, start: date, end: date) -> pd.DataFrame:
        fmt = "%d-%b-%Y"
        cinfo = (f"{{'name':'{name}','startDate':'{start.strftime(fmt)}',"
                 f"'endDate':'{end.strftime(fmt)}','indexName':'{name}'}}")
        r = self.s.post(self.URL, data=json.dumps({"cinfo": cinfo}),
                        headers={"Content-Type": "application/json; charset=utf-8"}, timeout=45)
        r.raise_for_status()
        rows = r.json()
        if isinstance(rows, dict) and "d" in rows:
            rows = json.loads(rows["d"])
        if not rows:
            return pd.DataFrame(columns=COLS)
        df = pd.DataFrame(rows)
        out = pd.DataFrame({
            "date": pd.to_datetime(df.get("HistoricalDate", df.get("HistoricalDate ")), format="mixed", dayfirst=True, errors="coerce"),
            "open": pd.to_numeric(df["OPEN"], errors="coerce"),
            "high": pd.to_numeric(df["HIGH"], errors="coerce"),
            "low": pd.to_numeric(df["LOW"], errors="coerce"),
            "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        })
        out["volume"] = pd.NA
        return out

    def history(self, name: str, start: date, end: date) -> pd.DataFrame:
        parts, cur = [], start
        while cur <= end:               # the site allows at most ~1 year per request
            stop = min(cur + timedelta(days=360), end)
            parts.append(self._chunk(name, cur, stop))
            cur = stop + timedelta(days=1)
            time.sleep(0.7)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
        return df.dropna(subset=["close"])


# ---------------------------------------------------------------- yahoo
def yahoo_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                     interval="1d", auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns=str.lower)
    df = df.rename(columns={"datetime": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    if "volume" not in df:
        df["volume"] = pd.NA
    return df[COLS].dropna(subset=["close"])


# ---------------------------------------------------------------- main logic
@dataclass
class Result:
    name: str
    source: str
    rows: int
    last: str | None
    error: str | None = None


def load(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    return pd.DataFrame(columns=COLS)


def update_index(item: dict, nse: NiftyIndices | None, today: date) -> Result:
    path = DATA / f"{slug(item['name'])}.csv"
    old = load(path)
    prev_source = None
    meta_path = path.with_suffix(".source")
    if meta_path.exists():
        prev_source = meta_path.read_text().strip()

    start = today - timedelta(days=365 * HISTORY_YEARS)
    if not old.empty:
        start = (old["date"].max() - timedelta(days=REFRESH_DAYS)).date()

    tries = []
    if item.get("nifty") and nse is not None:
        tries.append(("nifty", lambda s: nse.history(item["nifty"], s, today)))
    if item.get("yahoo"):
        tries.append(("yahoo", lambda s: yahoo_history(item["yahoo"], s, today)))
    if item.get("etf"):
        tries.append(("etf", lambda s: yahoo_history(item["etf"] + ".NS", s, today)))

    # never mix index points and ETF prices in one file: stick to the source already on file
    if prev_source:
        tries.sort(key=lambda t: 0 if t[0] == prev_source else 1)

    errors = []
    for src, fn in tries:
        if prev_source and src != prev_source and not old.empty:
            # switching source would mix different price scales; restart history from scratch
            s = today - timedelta(days=365 * HISTORY_YEARS)
            base = pd.DataFrame(columns=COLS)
        else:
            s, base = start, old
        try:
            new = fn(s)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {e}")
            continue
        if new.empty:
            errors.append(f"{src}: no data")
            continue
        df = pd.concat([base, new], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date", keep="last").sort_values("date")
        # some indices publish only a closing value on some days: fill the missing open/high/low with it
        df["close"] = pd.to_numeric(df["close"], errors="coerce").astype(float)
        for col in ("open", "high", "low"):
            v = pd.to_numeric(df[col], errors="coerce").astype(float)
            df[col] = v.where(v.notna() & (v > 0), df["close"])
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
        df = df.dropna(subset=["date", "close"])
        df = df[df["close"] > 0]
        if df.empty:
            errors.append(f"{src}: data unusable")
            continue
        DATA.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, date_format="%Y-%m-%d")
        meta_path.write_text(src)
        return Result(item["name"], src, len(df), df["date"].max().strftime("%Y-%m-%d"))
    return Result(item["name"], prev_source or "-", len(old),
                  None if old.empty else old["date"].max().strftime("%Y-%m-%d"), "; ".join(errors))


def run(items: list[dict]) -> list[Result]:
    today = date.today()
    try:
        nse = NiftyIndices()
    except Exception as e:  # noqa: BLE001
        print(f"niftyindices unavailable: {e}")
        nse = None
    results = []
    for it in items:
        try:
            r = update_index(it, nse, today)
        except Exception as e:  # noqa: BLE001 - one bad index must not stop the run
            r = Result(it["name"], "-", 0, None, f"crashed: {e}")
        flag = "OK " if not r.error else "ERR"
        print(f"{flag} {r.name:<45} {r.source:<6} rows={r.rows:<5} last={r.last} {r.error or ''}")
        results.append(r)
    return results


if __name__ == "__main__":
    import sys
    import yaml
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config/daily.yaml"
    items = yaml.safe_load(open(ROOT / cfg))["indices"]
    run(items)
