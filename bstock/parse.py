"""HTML parsing for B-Stock listing and auction-detail pages.

Every extraction here is anchored on a stable hook -- an element id, a class
name, a hidden input, a GTM ``dataLayer`` payload or a JSON-LD block -- rather
than on document position, so incidental markup changes do not break it.
"""

import html as html_mod
import json
import re
from email.utils import parsedate_to_datetime

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

_TILE_START = re.compile(r'<li\s+id="auction-')
_TAG = re.compile(r"(?s)<[^>]+>")
_SCRIPT_OR_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def text_of(fragment):
    """Strip tags from an HTML fragment and collapse whitespace."""
    if not fragment:
        return ""
    return " ".join(html_mod.unescape(_TAG.sub(" ", fragment)).split())


def parse_money(raw):
    """Parse a rendered price into ``(amount, currency_code)``.

    Handles the ``$9.09`` / ``€5,825.00`` / ``£1,234`` forms B-Stock renders.
    Returns ``(None, None)`` when there is no number to be found.
    """
    if not raw:
        return None, None
    text = html_mod.unescape(raw)

    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    if currency is None:
        match = re.search(r"\b(USD|EUR|GBP)\b", text)
        if match:
            currency = match.group(1)

    digits = re.sub(r"[^0-9.,\-]", "", text)
    if not re.search(r"\d", digits):
        return None, currency

    if "," in digits and "." in digits:
        # Whichever separator comes last is the decimal point.
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        tail = digits.split(",")[-1]
        # A 3-digit tail is a thousands group; anything else is a decimal.
        digits = digits.replace(",", "") if len(tail) == 3 else digits.replace(",", ".")

    try:
        return float(digits), currency
    except ValueError:
        return None, currency


def parse_int(raw):
    """Pull the first integer out of ``raw``, ignoring separators."""
    if raw is None:
        return None
    match = re.search(r"\d[\d,.\s]*", str(raw))
    if not match:
        return None
    try:
        return int(re.sub(r"[^\d]", "", match.group(0)))
    except ValueError:
        return None


def parse_end_time(raw):
    """Parse B-Stock's RFC-2822 ``data-end-time`` into an aware datetime."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(html_mod.unescape(raw).strip())
    except (TypeError, ValueError, IndexError):
        return None


def _first(pattern, text, group=1, flags=re.S):
    match = re.search(pattern, text, flags)
    return match.group(group) if match else None


# ---------------------------------------------------------------------------
# listing page
# ---------------------------------------------------------------------------

def parse_total(page):
    """Return the ``N Auction(s)`` total the toolbar reports, or ``None``."""
    raw = _first(r'class="amount[^"]*"[^>]*>\s*([^<]+)', page)
    return parse_int(raw) if raw else None


def has_no_live_auctions(page):
    """True when B-Stock is showing the "no auctions available" fallback.

    In that state the grid lists recently *closed* auctions, which is worth
    telling the caller about so an empty-looking result is not mistaken for a
    broken scrape.
    """
    return "No auctions available at this time" in page


def _tile_spans(page):
    """Yield the HTML of each auction tile on a listing page."""
    starts = [m.start() for m in _TILE_START.finditer(page)]
    for index, start in enumerate(starts):
        if index + 1 < len(starts):
            yield page[start:starts[index + 1]]
        else:
            # Last tile: stop at whichever list container closes first.
            end = len(page)
            for closer in ("</ol>", "</ul>"):
                found = page.find(closer, start)
                if found != -1:
                    end = min(end, found)
            yield page[start:end]


def _tile_datalayer(tile):
    """Extract the GTM ``select_item`` payload embedded in a tile.

    This is the most trustworthy source for extended retail (MSRP), unit count
    and currency, because B-Stock emits them as numbers rather than as
    formatted text.
    """
    blob = _first(r"'items'\s*:\s*\[\s*(\{.*?\})\s*\]", tile)
    if not blob:
        return {}

    def field(name, pattern):
        return _first(r"'" + name + r"'\s*:\s*" + pattern, blob)

    quoted = r'"((?:[^"\\]|\\.)*)"'
    name = field("item_name", quoted)
    out = {
        "item_id": field("item_id", r"'([^']*)'"),
        "title_tracking": html_mod.unescape(name.replace('\\"', '"')) if name else None,
        "msrp": None,
        "units": parse_int(field("quantity", r"([0-9]+)")),
        # NOTE: dataLayer's 'currency' is the marketplace default, not the
        # lot's currency (every EU tile reports GBP, including EUR lots), and
        # 'item_category' is always "Default Category". Both are ignored except
        # as a last-resort currency fallback.
        "currency_default": field("currency", quoted),
    }
    price = field("price", r"([0-9]*\.?[0-9]+)")
    if price:
        try:
            out["msrp"] = float(price)
        except ValueError:
            pass
    return out


def parse_tile(tile, marketplace=None):
    """Parse a single auction tile into a dict of raw listing fields."""
    auction_id = _first(r'data-auction-id="(\d+)"', tile)
    if not auction_id:
        auction_id = _first(r"auction/view/id/(\d+)", tile)
    if not auction_id:
        return None

    url = _first(r'href="([^"]*auction/view/id/\d+/?[^"]*)"', tile)
    if not url and marketplace is not None:
        url = marketplace.auction_url(auction_id)

    title = text_of(
        _first(r'class="product-name"\s*>\s*<a[^>]*>(.*?)</a>', tile) or ""
    )

    bid_raw = _first(
        r'class="current_bid"[^>]*>.*?class="price"[^>]*>\s*<strong>(.*?)</strong>',
        tile,
    )
    cpu_raw = _first(
        r"class=['\"]cost_per_unit['\"].*?class=\"price\".*?<strong>(.*?)</strong>",
        tile,
    )

    current_bid, bid_currency = parse_money(text_of(bid_raw))
    cost_per_unit, cpu_currency = parse_money(text_of(cpu_raw))

    lot_sku = _first(r'class="product-image product-image-([^"\s]+)"', tile)
    bids = parse_int(_first(r'id="bid_number\d*"[^>]*>([^<]*)<', tile))
    closes_at = parse_end_time(_first(r'data-end-time="([^"]+)"', tile))

    tracking = _tile_datalayer(tile)

    return {
        "auction_id": int(auction_id),
        "url": html_mod.unescape(url) if url else None,
        "title": title or tracking.get("title_tracking"),
        "lot_id": lot_sku,
        "current_bid": current_bid,
        "cost_per_unit": cost_per_unit,
        "bids": bids,
        "closes_at": closes_at,
        "currency": bid_currency or cpu_currency or tracking.get("currency_default"),
        "msrp": tracking.get("msrp"),
        "units": tracking.get("units"),
        "product_id": tracking.get("item_id"),
    }


def parse_listing(page, marketplace=None):
    """Parse every auction tile on a listing page."""
    rows = []
    for tile in _tile_spans(page):
        parsed = parse_tile(tile, marketplace)
        if parsed:
            rows.append(parsed)
    return rows


# ---------------------------------------------------------------------------
# sidebar facets
# ---------------------------------------------------------------------------

def parse_facets(page, filter_params):
    """Discover the sidebar filters a marketplace currently offers.

    Returns ``{param: [{"value", "label", "count"}, ...]}``. Reading these at
    runtime avoids hardcoding the numeric ids B-Stock assigns per marketplace.
    """
    import urllib.parse

    facets = {}
    seen = set()
    pattern = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    for href, label_html in pattern.findall(page):
        href = html_mod.unescape(href)
        query = urllib.parse.urlsplit(href).query
        if not query:
            continue
        params = urllib.parse.parse_qs(query)
        label = text_of(label_html)
        if not label:
            continue
        count = None
        count_match = re.search(r"\((\d[\d,]*)\)\s*$", label)
        if count_match:
            count = parse_int(count_match.group(1))
            label = label[: count_match.start()].strip()
        for param in filter_params:
            if param not in params:
                continue
            value = params[param][0]
            key = (param, value)
            if key in seen:
                continue
            seen.add(key)
            facets.setdefault(param, []).append(
                {"value": value, "label": label, "count": count}
            )
    return facets


# ---------------------------------------------------------------------------
# detail page
# ---------------------------------------------------------------------------

def parse_jsonld(page):
    """Parse the JSON-LD ``Product`` block, tolerating B-Stock's raw newlines.

    The site emits literal newlines inside JSON strings, which strict JSON
    rejects, so decoding runs with ``strict=False``.
    """
    for blob in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.S,
    ):
        try:
            data = json.JSONDecoder(strict=False).decode(blob.strip())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def _hidden_input(page, name):
    return _first(
        r'<input[^>]+name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', page
    ) or _first(
        r'<input[^>]+value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', page
    )


def _by_id(page, element_id):
    return _first(r'id="' + re.escape(element_id) + r'"[^>]*value="([^"]*)"', page)


def parse_detail(page):
    """Parse an auction detail page into supplementary fields."""
    product = parse_jsonld(page)
    offer = product.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}

    labelled = {}
    pairs = re.findall(
        r'class="auction-data-label"[^>]*>(.*?)</div>\s*'
        r'<div class="auction-data-content"[^>]*>(.*?)</div>',
        page,
        re.S,
    )
    for label_html, value_html in pairs:
        label = text_of(label_html)
        if label:
            labelled.setdefault(label, text_of(value_html))

    # Crumbs repeat their label (a hidden itemprop="name" span plus the visible
    # text), so read the microdata name rather than the rendered text.
    breadcrumbs = []
    crumb_block = _first(r'(?s)class="breadcrumbs".*?</div>', page, group=0)
    if crumb_block:
        for name in re.findall(r'itemprop="name"[^>]*>([^<]+)<', crumb_block):
            name = text_of(name)
            if name and name not in breadcrumbs:
                breadcrumbs.append(name)

    fixed = (_hidden_input(page, "isFixedPrice") or "").strip().lower()
    retail_price, _ = parse_money(_hidden_input(page, "retailPrice"))
    premium_pct = _by_id(page, "buyersPremiumPercent")
    unit_price, unit_currency = parse_money(_by_id(page, "pricePerUnitOrigin"))

    # B-Stock double-escapes entities here ("&amp;euro;"), so unescape until
    # the text stops changing rather than exactly once.
    description = product.get("description") or ""
    for _ in range(3):
        unescaped = html_mod.unescape(description)
        if unescaped == description:
            break
        description = unescaped
    description = " ".join(description.split())

    # The JSON-LD name carries an SEO suffix: "<title> | Closes: ... | <market>".
    detail_title = " ".join((product.get("name") or "").split())
    if "|" in detail_title:
        detail_title = detail_title.split("|", 1)[0].strip()

    detail = {
        "detail_title": detail_title or None,
        "description": description or None,
        "condition": offer.get("itemCondition"),
        "location": offer.get("availableAtOrFrom"),
        "country": offer.get("areaServed"),
        "detail_currency": offer.get("priceCurrency"),
        "detail_bid": parse_money(offer.get("price"))[0],
        "availability": (offer.get("availability") or "").rsplit("/", 1)[-1] or None,
        "category": breadcrumbs[-1] if breadcrumbs else None,
        "breadcrumbs": " > ".join(breadcrumbs) or None,
        "offer_type": (
            "Buy Now" if fixed == "true" else "Auction" if fixed == "false" else None
        ),
        "retail_price": retail_price or None,
        "buyers_premium_pct": float(premium_pct) if premium_pct else None,
        "buyers_premium_fee": parse_money(_hidden_input(page, "payment_premium_fee"))[0],
        "payment_fixed_fee": parse_money(_hidden_input(page, "payment_fixed_fee"))[0],
        "payment_percent_fee": parse_money(_hidden_input(page, "payment_percent_fee"))[0],
        "shipping_fee": parse_money(_hidden_input(page, "shipping_fee"))[0],
        "close_date_text": labelled.get("Close Date"),
        "additional_charges": labelled.get("Additional Charges"),
        "detail_cost_per_unit": unit_price,
    }
    if not detail["detail_currency"]:
        detail["detail_currency"] = unit_currency
    return detail
