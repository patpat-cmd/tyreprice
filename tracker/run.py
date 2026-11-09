"""Daily tyre price run: scrape -> match -> save history -> alert -> rebuild chart data.

Run locally:   python -m tracker.run            (prints emails instead of sending if SMTP isn't set)
               python -m tracker.run --dry-run  (scrape + show results, write nothing)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import smtplib
import statistics
import sys
from email.message import EmailMessage
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import retailers as R

ROOT = Path(__file__).resolve().parent.parent
PRICES_CSV = ROOT / "data" / "prices.csv"
ALERT_STATE = ROOT / "data" / "alerts_sent.json"
HEALTH_STATE = ROOT / "data" / "health.json"
SITE_DATA = ROOT / "docs" / "data.json"
ALERT_SETTINGS = ROOT / "docs" / "alerts.json"
FIELDS = ["date", "size", "tyre_id", "retailer", "price", "description", "url"]

RUNFLAT_RE = re.compile(
    r"run\s*-?\s*flat|\bssr|\brft\b|\brof\b|\bzps?\b|\br-f\b|\d{2}z?rf\d{2}|self.?support|\brsc\b|\bmoe\b|\bdsst\b|\bemt\b",
    re.I,
)
EV_WORDS = {"ev", "elect", "ion", "t0", "t1", "t2", "acoustic", "econtact", "eprimacy"}


# ------------------------------------------------------------------ matching
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _pattern(p: str) -> re.Pattern:
    """'pilotsport4s' -> matches 'PILOT SPORT 4 S' / 'Pilot Sport 4S' but not 'Pilot Sport 4 ST'."""
    chars = _norm(p)
    return re.compile(r"[^a-z0-9]*".join(map(re.escape, chars)) + r"(?![a-z0-9])")


def is_runflat(o: R.Offer) -> bool:
    return o.runflat or bool(RUNFLAT_RE.search(f"{o.model} {o.description}"))


def is_ev(o: R.Offer) -> bool:
    text = f"{o.model} {o.description}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    return o.ev or bool(words & EV_WORDS) or "eprimacy" in _norm(text) or "ev suitable" in text


def matches(tyre: dict, o: R.Offer) -> bool:
    if is_runflat(o):
        return False
    text = f"{o.model} {o.description}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    if any((w in text) if " " in w else (w.lower() in words) for w in tyre.get("exclude", [])):
        return False
    if tyre.get("ev_only") and not is_ev(o):
        return False
    brands = tyre.get("brands") or ([tyre["brand"]] if tyre.get("brand") else [])
    if brands and not any(_norm(b) in _norm(o.brand) for b in brands):
        return False
    pats = tyre.get("match") or []
    return not pats or any(_pattern(p).search(text) for p in pats)


# ------------------------------------------------------------------ scraping
def scrape(cfg: dict, health: dict, today: str) -> list[dict]:
    """Returns rows: cheapest matching offer per (tyre, retailer)."""
    rows = []
    tyre_scraper_hits = {name: 0 for name in R.TYRE_SCRAPERS}
    tyre_scraper_errors: dict[str, str] = {}
    for size in cfg["sizes"]:
        offers: list[R.Offer] = []
        want_ev = any(t.get("ev_only") for t in size["tyres"])
        for name, fn in R.SIZE_SCRAPERS.items():
            try:
                got = fn(size)
                if name == "Tyroola" and want_ev:
                    urls = {o.url for o in got}
                    for o in fn(size, ev_only=True):  # tags EV-rated tyres
                        if o.url in urls:
                            next(x for x in got if x.url == o.url).ev = True
                        else:
                            got.append(o)
                if not got:
                    raise RuntimeError("no tyres found - page layout may have changed")
                offers += got
                _health_ok(health, name, today)
                print(f"  {size['label']:<20} {name:<12} {len(got):>4} tyres")
            except Exception as e:  # one broken site must not stop the others
                _health_fail(health, name, today, f"{size['label']}: {e!r}")
                print(f"  {size['label']:<20} {name:<12} FAILED: {e!r}", file=sys.stderr)

        for tyre in size["tyres"]:
            candidates = list(offers)
            for name, fn in R.TYRE_SCRAPERS.items():
                try:
                    got = fn(size, tyre)
                    tyre_scraper_hits[name] += len(got)
                    candidates += got
                except Exception as e:
                    tyre_scraper_errors[name] = f"{tyre['name']}: {e!r}"
                    print(f"  {tyre['name']:<42} {name} FAILED: {e!r}", file=sys.stderr)
            tyre_offers = [o for o in candidates if matches(tyre, o) and o.in_stock is not False]
            for url in tyre.get("costco_urls", []):
                try:
                    o = R.costco_product(url)
                    if o:
                        tyre_offers.append(o)
                        _health_ok(health, "Costco", today)
                    else:
                        raise RuntimeError("no price on page")
                except Exception as e:
                    _health_fail(health, "Costco", today, repr(e))
            best: dict[str, R.Offer] = {}
            for o in tyre_offers:
                if o.retailer not in best or o.price < best[o.retailer].price:
                    best[o.retailer] = o
            for o in best.values():
                rows.append({"date": today, "size": size["id"], "tyre_id": tyre["id"], "retailer": o.retailer,
                             "price": f"{o.price:.2f}", "description": o.description.split(" /")[0].strip(),
                             "url": o.url})
    for name, hits in tyre_scraper_hits.items():
        if name in tyre_scraper_errors or hits == 0:
            _health_fail(health, name, today, tyre_scraper_errors.get(name, "no tyres found for any search"))
        else:
            _health_ok(health, name, today)
        print(f"  {'per-tyre search':<20} {name:<12} {hits:>4} tyres")
    return rows


def _health_ok(h, name, today):
    h.setdefault(name, {})
    h[name].update(last_ok=today, fails=0, last_error=None)


def _health_fail(h, name, today, err):
    s = h.setdefault(name, {})
    if s.get("last_fail") != today:
        s["fails"] = s.get("fails", 0) + 1
    s.update(last_fail=today, last_error=err[:300])


# ------------------------------------------------------------------ history
def load_history() -> list[dict]:
    if not PRICES_CSV.exists():
        return []
    with PRICES_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_history(rows: list[dict]) -> None:
    PRICES_CSV.parent.mkdir(exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["date"], r["size"], r["tyre_id"], float(r["price"])))
    with PRICES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def daily_best(history: list[dict], tyre_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in history:
        if r["tyre_id"] == tyre_id and (r["date"] not in out or float(r["price"]) < float(out[r["date"]]["price"])):
            out[r["date"]] = r
    return out


# ------------------------------------------------------------------ alerts
def find_alerts(cfg: dict, history: list[dict], today: str, sent: dict) -> list[dict]:
    a = cfg.get("alerts", {})
    pct = float(a.get("percent_below_average", 10))
    window = int(a.get("average_window_days", 30))
    min_days = int(a.get("min_history_days", 7))
    today_d = dt.date.fromisoformat(today)
    alerts = []
    for size in cfg["sizes"]:
        for tyre in size["tyres"]:
            best = daily_best(history, tyre["id"])
            if today not in best:
                continue
            now = best.pop(today)
            price = float(now["price"])
            reasons = []
            target = tyre.get("target_price")
            if target and price <= float(target):
                reasons.append(f"at or below your target price of ${float(target):.0f}")
            if tyre.get("sale_alerts", True) and len(best) >= min_days:
                recent = [float(r["price"]) for d, r in best.items()
                          if (today_d - dt.date.fromisoformat(d)).days <= window]
                if recent:
                    avg = statistics.mean(recent)
                    if price <= avg * (1 - pct / 100):
                        reasons.append(f"{(1 - price / avg) * 100:.0f}% below its {window}-day average of ${avg:.0f}")
                prev_low = min(float(r["price"]) for r in best.values())
                if a.get("all_time_low", True) and price < prev_low:
                    reasons.append(f"new all-time low (previous low ${prev_low:.0f})")
            if not reasons:
                continue
            last = sent.get(tyre["id"])
            if (last and (today_d - dt.date.fromisoformat(last["date"])).days < 7 and price >= float(last["price"])
                    and last.get("target") == target):
                continue  # already told you about this price (or a lower one) this week, with the same target
            alerts.append({"tyre": tyre["name"], "size": size["label"], "tyre_id": tyre["id"], "price": price,
                           "target": target,
                           "retailer": now["retailer"], "url": now["url"], "description": now["description"],
                           "reasons": reasons})
    return alerts


def health_warnings(health: dict, today: str) -> list[str]:
    out = []
    today_d = dt.date.fromisoformat(today)
    for name, s in health.items():
        if s.get("fails", 0) >= 3:
            lw = s.get("last_warned")
            if not lw or (today_d - dt.date.fromisoformat(lw)).days >= 7:
                out.append(f"{name} has failed {s['fails']} days in a row. Last error: {s.get('last_error')}")
                s["last_warned"] = today
    return out


# ------------------------------------------------------------------ email
def send_email(subject: str, html: str, text: str) -> None:
    user, pw, to = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"), os.getenv("ALERT_TO")
    if not (user and pw and to):
        print("\n[email not configured - would have sent]\nSubject:", subject, "\n", text)
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465"))) as s:
        s.login(user, pw)
        s.send_message(msg)
    print("Email sent:", subject)


def alert_email(alerts: list[dict], warnings: list[str], site_url: str | None) -> tuple[str, str, str]:
    if alerts:
        cheapest = min(alerts, key=lambda x: x["price"])
        subject = (f"Tyre sale: {cheapest['tyre']} ${cheapest['price']:.0f} at {cheapest['retailer']}"
                   + (f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""))
    else:
        subject = "Tyre tracker: a price source needs attention"
    rows_html, rows_text = [], []
    for a in alerts:
        rows_html.append(
            f"<tr><td><b>{escape(a['tyre'])}</b><br><small>{escape(a['size'])}</small></td>"
            f"<td style='font-size:20px'><b>${a['price']:.0f}</b></td>"
            f"<td><a href='{escape(a['url'])}'>{escape(a['retailer'])}</a><br><small>{escape(a['description'])}</small></td>"
            f"<td>{'<br>'.join(escape(r) for r in a['reasons'])}</td></tr>")
        rows_text.append(f"- {a['tyre']} ({a['size']}): ${a['price']:.0f} at {a['retailer']} - "
                         f"{'; '.join(a['reasons'])}\n  {a['url']}")
    html = ""
    if alerts:
        html += ("<p>Price drops on tyres you're tracking (price per tyre):</p>"
                 "<table cellpadding='8' style='border-collapse:collapse' border='1'>"
                 "<tr><th>Tyre</th><th>Price</th><th>Where</th><th>Why</th></tr>" + "".join(rows_html) + "</table>")
    if warnings:
        html += "<p><b>Scraper problems:</b></p><ul>" + "".join(f"<li>{escape(w)}</li>" for w in warnings) + "</ul>"
    if site_url:
        html += f"<p><a href='{escape(site_url)}'>See price history charts</a></p>"
    text = "\n".join(rows_text) + ("\n\nScraper problems:\n" + "\n".join(warnings) if warnings else "")
    if site_url:
        text += f"\n\nCharts: {site_url}"
    return subject, html, text


# ------------------------------------------------------------------ chart data
def build_site_data(cfg: dict, history: list[dict], today: str, health: dict) -> dict:
    sizes = []
    for size in cfg["sizes"]:
        tyres = []
        for tyre in size["tyres"]:
            rows = [r for r in history if r["tyre_id"] == tyre["id"]]
            series: dict[str, list] = {}
            for r in sorted(rows, key=lambda r: r["date"]):
                series.setdefault(r["retailer"], []).append([r["date"], float(r["price"])])
            latest_date = max((r["date"] for r in rows), default=None)
            latest = sorted(({"retailer": r["retailer"], "price": float(r["price"]), "url": r["url"],
                              "description": r["description"]} for r in rows if r["date"] == latest_date),
                            key=lambda x: x["price"])
            best = daily_best(history, tyre["id"])
            lows = [float(r["price"]) for r in best.values()]
            tyres.append({"id": tyre["id"], "name": tyre["name"], "series": series, "latest": latest,
                          "latest_date": latest_date, "all_time_low": min(lows) if lows else None,
                          "target_price": tyre.get("target_price"), "sale_alerts": tyre.get("sale_alerts", True)})
        sizes.append({"id": size["id"], "label": size["label"], "tyres": tyres})
    return {"updated": today, "sizes": sizes,
            "health": {k: {"ok": v.get("fails", 0) == 0, "last_ok": v.get("last_ok")} for k, v in health.items()}}


# ------------------------------------------------------------------ alert settings from the web page
def apply_alert_settings(cfg: dict, path: Path = ALERT_SETTINGS) -> None:
    """docs/alerts.json is written by the 'Price alerts' panel on the chart page. It overrides config.yaml.

    {"rules": {"percent_below_average": 10, "all_time_low": true},
     "tyres": {"michelin-ps4s": {"target_price": 330, "sale_alerts": true}}}
    """
    if not path.exists():
        return
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"WARNING: ignoring unreadable {path.name}: {e}", file=sys.stderr)
        return
    rules = s.get("rules") or {}
    alerts = cfg.setdefault("alerts", {})
    if isinstance(rules.get("percent_below_average"), (int, float)) and 1 <= rules["percent_below_average"] <= 90:
        alerts["percent_below_average"] = rules["percent_below_average"]
    if isinstance(rules.get("all_time_low"), bool):
        alerts["all_time_low"] = rules["all_time_low"]
    per_tyre = s.get("tyres") or {}
    for size in cfg["sizes"]:
        for tyre in size["tyres"]:
            t = per_tyre.get(tyre["id"])
            if not isinstance(t, dict):
                continue
            if "target_price" in t:
                tp = t["target_price"]
                tyre["target_price"] = float(tp) if isinstance(tp, (int, float)) and tp > 0 else None
            if isinstance(t.get("sale_alerts"), bool):
                tyre["sale_alerts"] = t["sale_alerts"]


def send_test_email() -> int:
    """Sample alert built from the latest real prices, so you can see exactly what an alert looks like."""
    if not (os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD") and os.getenv("ALERT_TO")):
        print("ERROR: SMTP_USER, SMTP_PASSWORD and ALERT_TO must all be set to send email.", file=sys.stderr)
        return 1
    site = json.loads(SITE_DATA.read_text(encoding="utf-8"))
    alerts = []
    for size in site["sizes"]:
        for t in size["tyres"]:
            if t["latest"] and len(alerts) < 2:
                b = t["latest"][0]
                alerts.append({"tyre": t["name"], "size": size["label"], "price": b["price"], "retailer": b["retailer"],
                               "url": b["url"], "description": b["description"],
                               "reasons": ["TEST - this is what an alert looks like; no action needed"]})
    subject, html, text = alert_email(alerts, [], os.getenv("SITE_URL"))
    send_email("[TEST] " + subject, html, text)
    return 0


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="scrape and print, write/send nothing")
    ap.add_argument("--test-email", action="store_true", help="send a sample alert email (no scraping) and exit")
    args = ap.parse_args()
    if args.test_email:
        return send_test_email()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    apply_alert_settings(cfg)
    today = dt.datetime.now(ZoneInfo("Australia/Sydney")).date().isoformat()
    health = json.loads(HEALTH_STATE.read_text()) if HEALTH_STATE.exists() else {}
    active = {*R.SIZE_SCRAPERS, *R.TYRE_SCRAPERS, "Costco"}
    health = {k: v for k, v in health.items() if k in active}  # forget retailers that were switched off
    sent = json.loads(ALERT_STATE.read_text()) if ALERT_STATE.exists() else {}

    print(f"Tyre price run for {today}")
    rows = scrape(cfg, health, today)
    history = [r for r in load_history() if r["date"] != today] + rows  # re-runs replace today's rows

    print("\nToday's best prices:")
    for size in cfg["sizes"]:
        for tyre in size["tyres"]:
            mine = sorted((r for r in rows if r["tyre_id"] == tyre["id"]), key=lambda r: float(r["price"]))
            where = ", ".join(f"{r['retailer']} ${float(r['price']):.0f}" for r in mine) or "not found anywhere"
            print(f"  {tyre['name']:<42} {where}")

    alerts = find_alerts(cfg, history, today, sent)
    warnings = health_warnings(health, today)
    if args.dry_run:
        print("\n[dry run] alerts:", json.dumps(alerts, indent=1), "\nwarnings:", warnings)
        return 0

    save_history(history)
    SITE_DATA.parent.mkdir(exist_ok=True)
    SITE_DATA.write_text(json.dumps(build_site_data(cfg, history, today, health), indent=1), encoding="utf-8")

    if alerts or warnings:
        send_email(*alert_email(alerts, warnings, os.getenv("SITE_URL")))
        for a in alerts:
            sent[a["tyre_id"]] = {"date": today, "price": a["price"], "target": a["target"]}
    ALERT_STATE.write_text(json.dumps(sent, indent=1))
    HEALTH_STATE.write_text(json.dumps(health, indent=1))
    print(f"\nSaved {len(rows)} prices. Alerts: {len(alerts)}. Warnings: {len(warnings)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
