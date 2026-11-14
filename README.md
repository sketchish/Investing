# Sector Tracker

Tracks Indian sector indices as candle charts and sends alerts. Everything is free and open source,
and runs automatically on GitHub — your computer does not need to be on.

**Charts:** https://sketchish.github.io/sector-tracker/

## What runs when

| When (IST) | What happens |
|---|---|
| Mon–Fri, 6:15 pm | Downloads today's daily candle for the ~50 sectors in `config/daily.yaml`, checks the **daily** alert rules, updates the charts |
| Saturday, 8:45 am | Checks the **weekly** alert rules on the finished week and sends a weekly review of all 50 sectors |
| 1st of every month, 7:00 am | Downloads **all** sector indices (`config/monthly.yaml`), checks **monthly** rules, sends an all-sector review |

You can also run any of these by hand: **Actions → Sector tracker → Run workflow**.

## Where the data comes from

1. **NSE index history** (niftyindices.com) — official candles for every Nifty index.
2. **Yahoo Finance** — backup for the main Nifty indices.
3. **The ETF's own price** — used for BSE indices, gold and silver (no free index feed). These are marked "ETF" on the charts.

Candle files are kept in `data/daily/`. Weekly and monthly candles are built from them.

## Alerts

Rules live in `config/rules.yaml`, written in plain words like:

```yaml
- name: Close crossed ABOVE the 50-day average
  timeframe: daily
  when: cross_above(close, sma(close, 50))
```

When a rule fires, a GitHub issue is opened in this repo, and **GitHub emails you** about it.

### Optional: direct email
Add these under **Settings → Secrets and variables → Actions → New repository secret**:
`SMTP_HOST` (e.g. `smtp.gmail.com`), `SMTP_USER`, `SMTP_PASSWORD` (a Gmail *app password*), `ALERT_EMAIL_TO`.

### Optional: WhatsApp
Uses the free CallMeBot service for personal alerts. Get a key at callmebot.com (WhatsApp section), then add secrets
`CALLMEBOT_PHONE` (with country code, e.g. `+91…`) and `CALLMEBOT_APIKEY`.

## Not investment advice
Signals are mechanical checks on past prices. Always verify before acting.
