# Project memory — Investing (sector tracker)

**Read this first in every new session.** It is the handoff note so work can resume where it stopped.
Keep it up to date: at the end of any session that changes something, update the "Where we left off"
and "Decision log" sections, then commit and push.

- Repo: https://github.com/sketchish/Investing (remote must be the lowercase URL `https://github.com/sketchish/investing`)
- Live site: https://sketchish.github.io/Investing/ (old `/sector-tracker/` link is dead — accepted)
- ETF list (Claude Sheet): https://claude.ai/artifact/K1nRfeATH3m4QWdN8eb5m6

## Who I'm working with

- **Chirag**, Ahmedabad. Calls himself non-technical — explain in plain language, step by step.
- **Always commit and push to Git** after every change (he does not commit himself).
- Everything must be **free / open source**. He avoids ready-made platforms because they don't allow his own alerts.
- He **teaches the setups one at a time**; Claude turns each into a named trigger, tests it on past charts,
  shows him the results and gets his "yes" **before** wiring it into alerts.
- Watches mostly on the **phone** (also laptop). Uses TradingView alongside this site.

## His chart setup

- **Hollow candles** always, **RSI 14** panel (70/30), **no grid**, full-width chart on desktop, draggable price/time scale,
  date + OHLC on hover, ≥5 years of history (10 years loaded).
- One moving average only: **EMA 5**, source close, same timeframe as the chart, wait for candle close.

## What exists (all done)

1. List of all Indian sector / thematic / commodity indices.
2. Sector ETFs: best 2–3 per index (liquid first, then cheapest) — in the Claude Sheet above.
3. Candle tracking via GitHub Actions: ~50 sectors daily (`config/daily.yaml`), all ~85+ monthly (`config/monthly.yaml`).
4. Alerts by email = GitHub issue emails (WhatsApp via CallMeBot is wired but needs his secrets — optional).
5. Phone-first website (`site/index.html`): tabs Today / Rotation / Watchlist / Chart.
6. **Trigger 1 — OTR (Old Top Retest)** — live. His priority trigger.
7. **Trigger 2 — PLT (Previous Low Test)** — live (15 Nov 2026).
8. Today screen = one section per named trigger (`TRIGGERS` array in `site/index.html`), big readable cards;
   all sectors hidden behind "Show all sectors".

## The triggers (his rules, in his words turned into code)

Always show the **short name with the full name**, e.g. "PLT — Previous Low Test".

### OTR — Old Top Retest (`tracker/otr.py`, settings `otr:` in `config/watch.yaml`)
Possible bottom: price comes back to test an **untested old rally top** on the **weekly** chart.
- Level = weekly **5 EMA top** (local top within ±4 weeks; flat consolidations count) and its price high. Only real
  highs count (must be higher than the previous EMA top — lower highs ignored).
- Must have broken out (weekly close and EMA above) and moved away ≥10% before a test counts.
- **Heads-up email**: first day price (daily low) or the weekly EMA comes within **2%**. Test = within 1.5%.
- Price test = normal; **EMA test = "deep buy"** (his rule). Broken = EMA >3% below the level.
- Verified with him on Nifty IT (Mar 2020, Jun 2022, Jul 2026) and FMCG (Oct 2018, Feb 2022, Sep–Oct 2026).

### PLT — Previous Low Test (`tracker/plt.py`, settings `plt:` in `config/watch.yaml`)
Second chance if the OTR bottom was missed.
- After an OTR test → OTR bottom (major weekly 5 EMA low) → first rally makes a **recent high** (major EMA top, 5% zigzag)
  → **previous low** = nearest major EMA swing low **below** the recent high (his option "b"), current cycle only, at or above the bottom.
- **Heads-up email**: first day price comes within **2%** of the previous low (main signal).
- **Caution email** (Fridays): weekly 5 EMA also reaches the low (within 1.5%).
- Backtest (10 yrs, ~100 indices, excl. Covid crash): price-only tests held 20 of 22; when the EMA also reached the
  low only 14 of 40 held (usually breaks first, then bounces). So for PLT the **price test is the strong one** —
  opposite of OTR. He accepted this.
- Example he confirmed: Nifty IT 2026 — bottom 27,762 (Jul), recent high 30,743 (Aug), PLT heads-up 23 Sep (low 28,229).
- **No targets** for now (he said so).

## How it runs

- `.github/workflows/tracker.yml` (UTC cron): daily Mon–Fri 12:40 (6:10 pm IST), weekly Sat 03:10, monthly 1st 01:25.
  Manual modes: `daily, weekly, monthly, site, otr-test, plt-test`.
- Code: `tracker/fetch.py` (niftyindices.com → yahoo → ETF price fallback), `indicators.py`, `rules.py`, `notify.py`,
  `otr.py`, `plt.py`, `main.py` (site data + alerts + reports). `site/data` is built in the run, not committed.
- Daily run only checks the **daily list**; monthly-only sectors (e.g. Nifty Hospitals, NBFC, Insurance) refresh
  once a month and **don't send OTR/PLT emails**.

## Working notes for Claude

- The workspace can't reach NSE/Yahoo/github.io — data comes from GitHub Actions; check the live site in his built-in browser.
- `gh` GraphQL is blocked: use REST, e.g.
  `gh api -X POST repos/sketchish/Investing/actions/workflows/tracker.yml/dispatches -f ref=main -f 'inputs[mode]=site'`.
- Push: `git pull --rebase origin main` (the bot commits data) then `git -c push.negotiate=true push origin HEAD:main`.
- After editing `site/index.html`, syntax-check the script with `node --check` before pushing.

## Where we left off (15 Nov 2026)

- OTR and PLT both live and tested (test emails: issue #5 OTR, issue #6 PLT). Cards enlarged on Today.
- He asked where to find Nifty Hospitals on TradingView: likely not listed there (new NSE index). BSE Hospitals is a
  different index (levels don't match); ETF `NSE:GROWWHOSPI` follows BSE Hospitals.

## Where we left off (17 Nov 2026)

- He noticed no emails: GitHub had **never** started a scheduled run (all runs were manual). Re-registered the
  schedule by changing the cron minutes (12:40 / 03:10 / 01:25 UTC) and ran the daily check by hand.
  Daily emails only go out when a trigger fires (quiet days = no email). Verify scheduled runs appear
  (`gh api "repos/sketchish/Investing/actions/runs?event=schedule"`); if still none, add a backup dispatcher.

## Open / next

- [ ] **Trigger 3** — he will teach it next.
- [ ] Offered, not answered: move every sector that appears on OTR/PLT lists (Hospitals, NBFC, Insurance, Capital Goods,
      AI Catalysts, FS 25/50, Midsmall Consumption, Non-Cyclical Consumer, Nifty100 Liquid 15) into the daily list so they get emails.
- [ ] Unanswered: should an old consolidation **zone** (e.g. IT 2021, 25,539–27,450) count as one OTR level that can be retested again?
- [ ] MA alerts paused (`ma_alerts: {}`) until he defines them. Watchlist is empty.
- [ ] WhatsApp: add `CALLMEBOT_PHONE` / `CALLMEBOT_APIKEY` secrets if he wants it.

## Decision log

- 14 Nov 2026 — OTR adopted as trigger 1; Today screen organised by trigger.
- 17 Nov 2026 — schedule re-registered after it never fired.
- 15 Nov 2026 — PLT adopted as trigger 2 (option b previous low; price test = strong, EMA test = caution); full names shown with short names; bigger cards.
