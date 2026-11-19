# Tyre price tracker (Sydney)

Checks tyre prices once a day, keeps the history and draws charts. Prices are per tyre when you buy a set of 4,
so multi-buy deals (e.g. "buy 3 get 1 free", "30% off 4") are counted and shown with a 🏷 deal badge.

- **Runs:** every morning ~7am Sydney time on GitHub Actions (free, PC can be off)
- **Retailers:** Tyroola, Tempe Tyres, MSWT, Bob Jane, Costco (searched by size), AC Tyres (Sydney store; prices exclude fitting)
- **Charts:** `https://<your-github-username>.github.io/<repo-name>/`
- **History:** `data/prices.csv` (one row per tyre / retailer / day — opens in Excel; `price4` / `deal` columns hold set-of-4 deals)
- **Tracked:** 225/40R18 sporty — Continental SportContact 7, Pirelli P Zero PZ5, Bridgestone Potenza Sport EVO,
  Michelin Pilot Sport 4S, Yokohama ADVAN Neova AD09.
  235/45R18 EV touring — Michelin e.Primacy, Hankook iON evo, Michelin Primacy 5 Energy (EV variant only),
  Goodyear EfficientGrip Performance 2, Bridgestone Turanza T005 EV, Pirelli Cinturato P7C2, Yokohama BluEarth-GT AE51.
  A blank card means no retailer has that tyre in stock in that size today.

## Emails

**Price alert emails are switched off** (`alerts: enabled: false` in `config.yaml`) and the alert controls are
hidden on the chart page (`SHOW_ALERTS = false` in `docs/index.html`). You still get one warning email if a
retailer's scraper breaks 3 days in a row (repeats weekly until fixed).

To turn price alerts back on: set `enabled: true` and `SHOW_ALERTS = true`. They then fire, per tyre, when the
cheapest price that day is 10%+ below its 30-day average, a new all-time low (after 7 days of history), or at/below
a target price set on the chart page (needs a GitHub token with Contents/Variables/Actions read-write, pasted into
Alert settings; stored only in that browser).

## Changing what's tracked

Edit `config.yaml` (on GitHub: open it, click the pencil). Each tyre has a `brand` and `match` keywords;
matching ignores case/spaces, so `pilotsport4s` matches "PILOT SPORT 4 S". Run-flats are always excluded.

## Notes on prices

- **Tempe Tyres** and **MSWT** prices include fitting & balancing.
- **Tyroola** prices are per tyre delivered; fitting is free on sets of 4 for some promos, otherwise extra.
- **Costco** needs membership; price shown is the member price. Its "$X off a set of 4" promos are counted;
  Shop Card offers are shown as the deal text but not deducted.
- **Deals:** Tyroola and Costco promos are read automatically. Bob Jane's multi-buy promos can't be - its product
  pages return HTTP 429 to the tracker - so Bob Jane prices shown are the single-tyre price.
- **Bob Jane** is searched per tyre (its Shopify search), so it costs ~10 small requests a day.
- **St George Tyres** (Arncliffe): scraper written (`st_george` in `tracker/retailers.py`) but switched off -
  the site returns HTTP 403 to GitHub's servers, and it stocked none of the tracked tyres when checked.
- **AC Tyres** uses the site's own product-list API for its Sydney store. Its scripts fingerprint visitors for
  analytics but don't block bots - they send searches tagged `operator: "#"`, and so do we. Prices exclude fitting.
- Not included: JAX Tyres (robots.txt disallows automated access), Tyrepower (quote-only, no prices online),
  Tyreright (prices load via JavaScript only), Beaurepaires (site unreachable at build time).
- Scrapers use an honest user-agent, respect robots.txt and make ~50 requests per day in total.
  If a site shows a CAPTCHA / bot check, it's skipped that day - never bypassed. Tempe Tyres has
  shown one at times; if it keeps doing so you'll get the "needs attention" email after 3 days.

## Setup (one-off)

1. Create a **public** GitHub repo and push this folder (Pages is free only for public repos —
   your email address is kept in a secret, not in the code).
2. **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `SMTP_USER` — the Gmail address that sends the emails
   - `SMTP_PASSWORD` — a Gmail **App Password** for that account
     (Google Account → Security → 2-Step Verification must be on → App passwords → create one)
   - `ALERT_TO` — where emails go
3. **Settings → Pages →** Source: *Deploy from a branch*, Branch: `main`, folder `/docs`.
4. **Actions** tab → *Daily tyre prices* → **Run workflow** to test it now.

## Run locally

```
pip install -r requirements.txt
python -m tracker.run --dry-run   # scrape and print, change nothing
python -m tracker.run             # full run (prints the email instead of sending unless SMTP_* env vars set)
```
