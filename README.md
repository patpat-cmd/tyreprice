# Tyre price tracker (Sydney)

Checks tyre prices once a day, keeps the history, draws charts, and emails you when there's a sale.

- **Runs:** every morning ~7am Sydney time on GitHub Actions (free, PC can be off)
- **Retailers:** Tyroola, Tempe Tyres, MSWT, Bob Jane, Costco (PS4S product page)
- **Charts:** `https://<your-github-username>.github.io/<repo-name>/`
- **History:** `data/prices.csv` (one row per tyre / retailer / day — opens in Excel)

## Setting alerts (on the chart page)

Each tyre card has **"🔔 Alert me at or below $___"** and a **sale alerts** tickbox. **Alert settings** (top right)
holds the email address(es), the % rule and the all-time-low toggle. The first time, connect a GitHub token:

1. https://github.com/settings/personal-access-tokens/new → *Only select repositories* → `tyreprice`
2. Repository permissions → **Contents**, **Variables**, **Actions**: *Read and write* → Generate
3. Paste it into Alert settings → Connect. It's stored only in that browser.

Saving writes target prices to `docs/alerts.json` (public, like the prices) and the email to the private
`ALERT_TO` Actions variable (not visible to visitors). Changes apply at the next daily check, or press
**Check prices now**.

## What triggers an email

Per tyre, using the cheapest price across all retailers that day:

- **10%+ below its 30-day average**, or
- **a new all-time low** (both only after 7 days of history, so week one is quiet), or
- **at/below your target price** set on the chart page (works from day one)

You won't get re-emailed about the same price within a week — only if it drops further or you change the target.
If a retailer's scraper breaks 3 days in a row you get one warning email (repeats weekly until fixed).

## Changing what's tracked

Edit `config.yaml` (on GitHub: open it, click the pencil). Each tyre has a `brand` and `match` keywords;
matching ignores case/spaces, so `pilotsport4s` matches "PILOT SPORT 4 S". Run-flats are always excluded.

## Notes on prices

- **Tempe Tyres** and **MSWT** prices include fitting & balancing.
- **Tyroola** prices are per tyre delivered; fitting is free on sets of 4 for some promos, otherwise extra.
- **Costco** needs membership; price shown is the member price.
- **Bob Jane** is searched per tyre (its Shopify search), so it costs ~10 small requests a day.
- **St George Tyres** (Arncliffe): scraper written (`st_george` in `tracker/retailers.py`) but switched off -
  the site returns HTTP 403 to GitHub's servers, and it stocked none of the tracked tyres when checked.
- Not included: JAX Tyres (robots.txt disallows automated access), AC Tyres (shop uses browser
  fingerprinting / bot checks), Tyrepower / Tyreright (prices load via JavaScript only),
  Beaurepaires (site unreachable at build time).
- Scrapers use an honest user-agent, respect robots.txt and make ~50 requests per day in total.
  If a site shows a CAPTCHA / bot check, it's skipped that day - never bypassed. Tempe Tyres has
  shown one at times; if it keeps doing so you'll get the "needs attention" email after 3 days.

## Setup (one-off)

1. Create a **public** GitHub repo and push this folder (Pages is free only for public repos —
   your email address is kept in a secret, not in the code).
2. **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `SMTP_USER` — the Gmail address that sends the alerts (e.g. patkong@gmail.com)
   - `SMTP_PASSWORD` — a Gmail **App Password** for that account
     (Google Account → Security → 2-Step Verification must be on → App passwords → create one)
   - `ALERT_TO` — where alerts go (patkong@gmail.com)
3. **Settings → Pages →** Source: *Deploy from a branch*, Branch: `main`, folder `/docs`.
4. **Actions** tab → *Daily tyre prices* → **Run workflow** to test it now.

## Run locally

```
pip install -r requirements.txt
python -m tracker.run --dry-run   # scrape and print, change nothing
python -m tracker.run             # full run (prints the email instead of sending unless SMTP_* env vars set)
```
