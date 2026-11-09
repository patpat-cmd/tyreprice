# Tyre price tracker (Sydney)

Checks tyre prices once a day, keeps the history, draws charts, and emails you when there's a sale.

- **Runs:** every morning ~7am Sydney time on GitHub Actions (free, PC can be off)
- **Retailers:** Tyroola, Tempe Tyres, MSWT, Costco (PS4S product page)
- **Charts:** `https://<your-github-username>.github.io/<repo-name>/`
- **History:** `data/prices.csv` (one row per tyre / retailer / day — opens in Excel)

## What triggers an email

Per tyre, using the cheapest price across all retailers that day:

- **10%+ below its 30-day average**, or
- **a new all-time low** (both only after 7 days of history, so week one is quiet), or
- **at/below a `target_price`** if you set one in `config.yaml` (works from day one)

You won't get re-emailed about the same price within a week — only if it drops further.
If a retailer's scraper breaks 3 days in a row you get one warning email (repeats weekly until fixed).

## Changing what's tracked

Edit `config.yaml` (on GitHub: open it, click the pencil). Each tyre has a `brand` and `match` keywords;
matching ignores case/spaces, so `pilotsport4s` matches "PILOT SPORT 4 S". Run-flats are always excluded.

## Notes on prices

- **Tempe Tyres** and **MSWT** prices include fitting & balancing.
- **Tyroola** prices are per tyre delivered; fitting is free on sets of 4 for some promos, otherwise extra.
- **Costco** needs membership; price shown is the member price.
- Not included: JAX Tyres (its robots.txt disallows automated access), Tyrepower / Tyreright / Bob Jane
  (prices load via JavaScript only), Beaurepaires (site unreachable at build time).
  Scrapers use an honest user-agent, respect robots.txt and make ~25 requests per day in total.

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
