"""Retailer scrapers.

Each scraper takes a size dict (width/profile/rim) and returns a list of Offer objects
for every tyre the retailer lists in that size. Matching to the tyres you care about
happens later in match.py, so scrapers stay dumb and easy to fix when a site changes.

Rules we follow: honest User-Agent, robots.txt respected, a few requests per day, no
bot-check circumvention. If a site blocks plain requests, we simply don't use it.
"""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = "tyreprice/1.0 (personal once-a-day tyre price tracker; +https://github.com)"
TIMEOUT = 40
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


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


class RobotsDisallowed(Exception):
    pass


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
    resp.raise_for_status()
    time.sleep(2)  # be polite
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
            offers.append(Offer(
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


# ---------------------------------------------------------------- Costco (specific product pages)
def costco_product(url: str) -> Offer | None:
    soup = BeautifulSoup(fetch(url).text, "html.parser")
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(d.get("@type", "")).lower() != "product":
            continue
        offers = d.get("offers") or {}
        price = offers.get("price")
        if price is None:
            specs = offers.get("priceSpecification") or []
            prices = [float(p["price"]) for p in specs if isinstance(p, dict) and p.get("price")]
            price = min(prices) if prices else None
        if price is None:
            continue
        name = d.get("name", "")
        return Offer(
            retailer="Costco",
            brand=name.split(" ")[0],
            model=name,
            description=name,
            price=float(price),
            url=url,
            extra={"members_only": True},
        )
    return None


SIZE_SCRAPERS = {
    "Tyroola": tyroola,
    "Tempe Tyres": tempe,
    "MSWT": mswt,
}
