"""Retailer scrapers.

Size scrapers (SIZE_SCRAPERS) take a size dict (width/profile/rim) and return a list of Offer
objects for every tyre the retailer lists in that size; per-tyre scrapers (TYRE_SCRAPERS, e.g.
Bob Jane) take (size, tyre) and search for that one tyre. Matching to the tyres you care about
happens later in run.py, so scrapers stay dumb and easy to fix when a site changes.

Rules we follow: honest User-Agent, robots.txt respected, a few requests per day, no
bot-check circumvention. If a site blocks plain requests, we simply don't use it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

USER_AGENT = "tyreprice/1.0 (personal once-a-day tyre price tracker; +https://github.com)"
TIMEOUT = 40
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
_SYDNEY = ZoneInfo("Australia/Sydney")


@dataclass
class Offer:
    retailer: str
    brand: str
    model: str
    description: str  # full name incl. size/load/speed, used for matching + display
    price: float  # AUD per tyre
    url: str
    in_stock: bool | None = None
    ev: bool = False
    runflat: bool = False
    extra: dict = field(default_factory=dict)
    deal: str | None = None  # multi-buy promo text, e.g. "Buy 3 get 1 free", "Save 20% on 4 tyres"
    price4: float | None = None  # AUD per tyre when buying a set of 4 with that deal (None = no discount)

    @property
    def set_price(self) -> float:
        """What one tyre effectively costs when you buy 4 - the number we rank and chart by."""
        return self.price4 if self.price4 and self.price4 < self.price else self.price


def deal_price4(text: str, price: float) -> float | None:
    """Per-tyre price when buying 4, worked out from promo wording. None if it doesn't lower the price
    (gift cards, free fitting, wording we don't understand)."""
    t = (text or "").lower()
    if m := re.search(r"buy\s*(\d)\D{0,6}get\s*(\d)\s*(?:free|for\s*\$?0)", t):  # buy 3 get 1 free
        paid, free = int(m[1]), int(m[2])
        return round(price * paid / (paid + free), 2) if paid + free == 4 else None
    if m := re.search(r"\b4\s*for\s*(?:the\s*price\s*of\s*)?(\d)\b", t):  # 4 for 3
        return round(price * int(m[1]) / 4, 2) if int(m[1]) < 4 else None
    if m := re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*off|save\s*(\d{1,2}(?:\.\d+)?)\s*%", t):  # save 45% / 45% off
        pct = float(m[1] or m[2])
        return round(price * (1 - pct / 100), 2) if 0 < pct < 90 else None
    if "gift" not in t and (m := re.search(r"\$\s*(\d+)\s*(?:off|cash\s*back|instant|discount)|save\s*\$\s*(\d+)", t)):
        off = float(m[1] or m[2])  # dollars off a set of 4
        return round(price - off / 4, 2) if off < price * 4 else None
    return None


class RobotsDisallowed(Exception):
    pass


class BotCheckBlocked(Exception):
    """The site showed a CAPTCHA / bot check. We never try to get past these - just skip the site today."""


def _allowed(url: str) -> bool:
    m = re.match(r"(https?://[^/]+)", url)
    root = m.group(1)
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
        try:
            resp = requests.get(root + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        _robots_cache[root] = rp
    return rp.can_fetch(USER_AGENT, url)


def fetch(url: str, *, accept: str = "text/html") -> requests.Response:
    if not _allowed(url):
        raise RobotsDisallowed(url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": accept}, timeout=TIMEOUT)
    time.sleep(2)  # be polite
    if resp.status_code in (403, 429) or "captcha" in resp.url.lower() or re.search(
            r"<title>[^<]*(verify you are human|just a moment|attention required|access denied)", resp.text[:5000], re.I):
        raise BotCheckBlocked(f"bot check / CAPTCHA shown (HTTP {resp.status_code}) - skipped today")
    resp.raise_for_status()
    return resp


def _price(text: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(m.group(0).replace(",", "")) if m else None


# ---------------------------------------------------------------- Tyroola (Sydney)
def tyroola(size: dict, ev_only: bool = False) -> list[Offer]:
    base = "https://www.tyroola.com.au"
    offers: list[Offer] = []
    page = 1
    while True:
        url = (f"{base}/api/v2/catalog/?product_type=tyre&width={size['width']}&height={size['profile']}"
               f"&diameter={size['rim']}&catalogRegion=nsw&pageSize=100&page={page}")
        if ev_only:
            url += "&ev=1"
        data = fetch(url, accept="application/json").json()
        for p in data.get("products", []):
            if p.get("price") is None:
                continue
            desc = f"{p.get('manufacturer', '')} {p.get('profile', '')} {p.get('dimensions', '')}"
            slug = p.get("url", "")
            deal, price4 = _tyroola_deal(p.get("badges") or [], p["price"] / 100)
            offers.append(Offer(
                deal=deal, price4=price4,
                retailer="Tyroola",
                brand=p.get("manufacturer", ""),
                model=p.get("profileText") or p.get("profile", ""),
                description=desc + " " + slug.replace("-", " "),
                price=p["price"] / 100,
                url=urljoin(base, slug),
                in_stock=(p.get("stock") or {}).get("available"),
                ev=ev_only or "ev-suitable" in slug or "elect" in slug,
                runflat=bool(p.get("rft")),
                extra={"rrp": (p["priceRrp"] / 100) if p.get("priceRrp") else None},
            ))
        pag = data.get("pagination") or {}
        if page >= (pag.get("totalPages") or 1) or page >= 10:
            break
        page += 1
    return offers


def _tyroola_deal(badges: list, price: float) -> tuple[str | None, float | None]:
    """Tyroola badges look like {"text": "Save 20% on 4 tyres", "type": "save-percent", "discountValue": 20,
    "matchedProdQtyMultiplier": 4}. Types seen: save-percent, instant-cash (cents off the set), gift-card."""
    for b in badges:
        if not isinstance(b, dict) or not b.get("text") or (b.get("matchedProdQtyMultiplier") or 4) > 4:
            continue
        text, kind, val = b["text"].strip(), b.get("type"), b.get("discountValue") or 0
        if kind == "save-percent" and 0 < val < 90:
            return text, round(price * (1 - val / 100), 2)
        if kind == "instant-cash" and 0 < val / 100 < price * 4:
            return text, round(price - val / 100 / 4, 2)
        return text, deal_price4(text, price)
    return None, None


# ---------------------------------------------------------------- Tempe Tyres (Sydney)
def tempe(size: dict, ev_only: bool = False) -> list[Offer]:
    base = "https://www.tempetyres.com.au"
    url = f"{base}/tyres?TyreWidth={size['width']}&TyreProfile={size['profile']}&TyreDiameter={size['rim']}"
    soup = BeautifulSoup(fetch(url).text, "html.parser")
    offers, seen = [], set()
    for box in soup.select("div.product-container"):
        form = box.find("form", action="/cart")
        if not form:
            continue
        vals = {i.get("name"): i.get("value") for i in form.find_all("input")}
        pid = vals.get("id")
        if not pid or pid in seen or not vals.get("price"):
            continue
        seen.add(pid)
        link = box.find("a", href=True)
        brand_el = box.select_one(".brand-name")
        model_el = box.select_one(".sub-heading-ty-3")
        desc = vals.get("description", "")
        stock = vals.get("stock")
        offers.append(Offer(
            retailer="Tempe Tyres",
            brand=brand_el.get_text(strip=True) if brand_el else desc.split(" ")[0],
            model=model_el.get_text(strip=True) if model_el else desc,
            description=desc,
            price=float(vals["price"]),
            url=urljoin(base, link["href"]) if link else url,
            in_stock=(int(stock) > 0) if stock and stock.isdigit() else None,
            extra={"fitted": True},
        ))
    return offers


# ---------------------------------------------------------------- MSWT (ships + fits nationally)
def mswt(size: dict, ev_only: bool = False) -> list[Offer]:
    base = f"https://mswt.com.au/search-tyres.php?width={size['width']}&profile={size['profile']}&rim={size['rim']}"
    offers: list[Offer] = []
    seen_urls: set[str] = set()
    for page in range(1, 16):  # 12 tyres per page; cap to stay polite
        page_offers = _mswt_page(f"{base}&page={page}")
        new = [o for o in page_offers if o.url not in seen_urls]
        if not new:
            break
        seen_urls.update(o.url for o in new)
        offers.extend(new)
    return offers


def _mswt_page(url: str) -> list[Offer]:
    soup = BeautifulSoup(fetch(url).text, "html.parser")
    offers = []
    for block in soup.select("div.product-block"):
        brand_img = block.select_one("img.brand")
        model_el = block.select_one(".model-matchheight b")
        spec_el = block.select_one(".product-model")
        link = block.select_one("a[itemprop=url]") or block.find("a", href=True)
        price_cell = block.select_one(".product-prices td.fs18")
        if not (brand_img and model_el and price_cell):
            continue
        # The visible price is the last <span> in the cell; struck-through RRP is commented out.
        spans = price_cell.find_all("span")
        price = _price(spans[-1].get_text()) if spans else _price(price_cell.get_text())
        if not price:
            continue
        brand = brand_img.get("alt", "").strip()
        model = model_el.get_text(" ", strip=True)
        spec = spec_el.get_text(" ", strip=True) if spec_el else ""
        offers.append(Offer(
            retailer="MSWT",
            brand=brand,
            model=model,
            description=f"{brand} {model} {spec}",
            price=price,
            url=link["href"] if link else url,
            in_stock="Available" in price_cell.get_text() or None,
            extra={"fitted": True},
        ))
    return offers


# ---------------------------------------------------------------- Costco (members only; SAP Commerce API)
def costco(size: dict, ev_only: bool = False) -> list[Offer]:
    """Size search gives names but no prices, so each in-size tyre's details are fetched (price + promotions)."""
    api = "https://www.costco.com.au/rest/v2/australia/products"
    q = f"{size['width']}/{size['profile']}R{size['rim']}"
    size_re = re.compile(rf"\b{size['width']}/{size['profile']}Z?R{size['rim']}\b", re.I)
    found = []
    for page in range(3):
        data = fetch(f"{api}/search?" + urlencode({"query": q, "pageSize": 50, "currentPage": page, "fields": "FULL",
                                                    "lang": "en_AU", "curr": "AUD"}), accept="application/json").json()
        for p in data.get("products", []):
            summary = {s.get("name"): s.get("frontValue") for s in p.get("tireSummary") or []}
            if size_re.search(p.get("name", "")) and summary.get("Runflat Tire") != "Y":
                found.append(p)
        if page + 1 >= (data.get("pagination") or {}).get("totalPages", 1):
            break
    offers = []
    for p in found:
        d = fetch(f"{api}/{p['code']}?fields=FULL&lang=en_AU&curr=AUD", accept="application/json").json()
        price = (d.get("price") or {}).get("value")
        if not price:
            continue
        name = d.get("name") or p["name"]
        deal, price4 = _costco_deal(d.get("potentialPromotions") or [], float(price))
        offers.append(Offer(
            retailer="Costco", brand=name.split(" ")[0], model=name, description=name, price=float(price),
            url="https://www.costco.com.au" + (d.get("url") or p.get("url", "")),
            in_stock=((d.get("stock") or {}).get("stockLevelStatus") != "outOfStock") and d.get("purchasable", True),
            deal=deal, price4=price4, extra={"members_only": True},
        ))
    return offers


def _costco_deal(promos: list, price: float) -> tuple[str | None, float | None]:
    """Costco promos are long sentences ("Purchase a set of 4 or more 18" Continental tyres and receive an instant
    $100 off at checkout. Promo valid ..."), so build a short label and keep the end date."""
    for pr in promos:
        text = (pr.get("description") or "").strip()
        if not text or pr.get("paymentPromotionFlag"):  # skip "pay with X card" offers
            continue
        until = ""
        if pr.get("endDate"):
            try:
                end = dt.datetime.fromisoformat(pr["endDate"].replace("Z", "+00:00")).astimezone(_SYDNEY)
                until = f" (until {end.day} {end:%b})"
            except ValueError:
                pass
        if m := re.search(r"\$\s*(\d+)\s*(?:costco\s*)?(shop\s*card|gift\s*card)", text, re.I):
            return f"${m[1]} {m[2].title()} on 4{until}", None
        price4 = deal_price4(text, price)
        if m := re.search(r"\$\s*(\d+)\s*off", text, re.I):
            return f"${m[1]} off a set of 4{until}", price4
        first = re.split(r"(?<=[.!])\s", text)[0]
        return (first[:80] + ("…" if len(first) > 80 else "")) + until, price4
    return None, None


# ---------------------------------------------------------------- AC Tyres (online; Sydney store; prices exclude fitting)
AC_SYDNEY_STORE = "999999240306904"  # AC Tyres keeps separate stock/prices per city; Sydney is the one we can use


def ac_tyres(size: dict, ev_only: bool = False) -> list[Offer]:
    """The site's own size-search API (the tyre pages load prices from it). Bots are told to send operator "#",
    which we do. The crossed-out 'couponprice' is a was-price, not a deal, so it's ignored."""
    url = "https://web.actyres.com.au//webapi/Product/ProductBasecontroller/queryList"
    if not _allowed("https://web.actyres.com.au/webapi/") or not _allowed("https://actyres.com.au/tagview/"):
        raise RobotsDisallowed(url)
    offers: list[Offer] = []
    for page in range(1, 11):
        body = {"storeid": AC_SYDNEY_STORE, "name": "", "ordertype": "", "width": str(size["width"]),
                "profile": str(size["profile"]), "rim": f"R{size['rim']}", "brand": "", "loadindex": "",
                "speedindex": "", "operator": "#", "startP": page, "stockid_ne_stock": "#", "priceasc": "priceasc",
                "endP": 50}
        resp = requests.post(url, json=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                             timeout=TIMEOUT)
        time.sleep(2)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) != "0":
            raise RuntimeError(f"AC Tyres API said: {data.get('message')}")
        lst = (data.get("data") or {}).get("list") or {}
        for x in lst.get("records") or []:
            if not x.get("price") or not x.get("id"):
                continue
            name = x.get("name") or x.get("model") or ""
            stock = float(x.get("stock") or 0)
            offers.append(Offer(
                retailer="AC Tyres", brand=x.get("brand", ""), model=x.get("model") or name,
                description=f"{x.get('brand', '').title()} {name} · fitting extra", price=float(x["price"]),
                url=f"https://actyres.com.au/product/{name.replace(' ', '-').replace('/', '-')}/?id={x['id']}",
                in_stock=stock > 0 if x.get("stockid") in (None, "", "#") else None,
                extra={"fitted": False},
            ))
        if page >= (lst.get("pages") or 1):
            break
    return offers


# ---------------------------------------------------------------- St George Tyres (Arncliffe, Sydney)
_stg_filters: dict[str, dict[str, str]] = {}


def st_george(size: dict, ev_only: bool = False) -> list[Offer]:
    """Uses the size filters on the /tyres.html category (its /catalogsearch/ is disallowed by robots.txt)."""
    base = "https://www.stgeorgetyres.com.au/tyres.html"
    if not _stg_filters:
        soup = BeautifulSoup(fetch(base).text, "html.parser")
        for a in soup.select("a[href*='?']"):
            m = re.search(r"\?(width|profile|diameter)=(\d+)$", a["href"])
            if m:
                label = a.get_text(" ", strip=True).split(" ")[0]  # "225 331 items" -> "225"
                _stg_filters.setdefault(m.group(1), {})[label] = m.group(2)
    try:
        q = (f"width={_stg_filters['width'][str(size['width'])]}&profile={_stg_filters['profile'][str(size['profile'])]}"
             f"&diameter={_stg_filters['diameter'][str(size['rim'])]}")
    except KeyError:
        raise RuntimeError(f"size filter not found on St George site for {size['width']}/{size['profile']}R{size['rim']}")
    offers, seen = [], set()
    for page in range(1, 16):
        soup = BeautifulSoup(fetch(f"{base}?{q}&p={page}").text, "html.parser")
        new = 0
        for it in soup.select(".product-item-info"):
            a = it.select_one("a.product-item-link")
            pr = it.select_one("[data-price-amount]")
            if not a or a["href"] in seen:
                continue
            seen.add(a["href"])
            new += 1
            if not pr:  # "call for price" items
                continue
            name = a.get_text(" ", strip=True)
            brand = name.split(" ")[0].title()
            offers.append(Offer(retailer="St George Tyres", brand=brand,
                                model=re.sub(r"(?i)^\w+\s+(tyres\s+)?", "", name), description=name,
                                price=float(pr["data-price-amount"]), url=a["href"]))
        if not new:
            break
    return offers


# ---------------------------------------------------------------- Bob Jane T-Marts (Shopify, per-tyre search)
def bob_jane(size: dict, tyre: dict) -> list[Offer]:
    """Bob Jane has no size listing we can use, so search per tyre via Shopify's suggest endpoint."""
    size_txt = f"{size['width']}/{size['profile']}R{size['rim']}"
    query = f"{tyre.get('search') or tyre['name']} {size_txt}"
    url = ("https://www.bobjane.com.au/search/suggest.json?" + urlencode(
        {"q": query, "resources[type]": "product", "resources[limit]": 10}))
    data = fetch(url, accept="application/json").json()
    size_re = re.compile(rf"\b{size['width']}/{size['profile']}Z?R{size['rim']}\b", re.I)
    offers = []
    for p in data.get("resources", {}).get("results", {}).get("products", []):
        title = p.get("title", "")
        if not size_re.search(title) or not p.get("price"):
            continue
        labels = [t.split(":", 1)[1] for t in p.get("tags", []) if t.startswith("label:")]
        offers.append(Offer(
            retailer="Bob Jane", brand=p.get("vendor", ""), model=title,
            description=f"{title} {' '.join(labels)}",
            price=float(p["price"]), url="https://www.bobjane.com.au/products/" + p["handle"],
            in_stock=p.get("available"), ev="Electric Vehicle" in labels, runflat="Run Flat" in labels,
        ))
    return offers


SIZE_SCRAPERS = {
    "Tyroola": tyroola,
    "Tempe Tyres": tempe,
    "MSWT": mswt,
    "Costco": costco,
    "AC Tyres": ac_tyres,
    # "St George Tyres": st_george,  # disabled: returns HTTP 403 to GitHub's servers (works from home PCs),
    #                                # and it stocked none of the tracked tyres when checked (Nov 2026).
}

# Retailers searched once per tyre (rather than once per size).
TYRE_SCRAPERS = {
    "Bob Jane": bob_jane,
}
