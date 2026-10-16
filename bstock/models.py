"""Flattening parsed fields into one row per lot, plus the derived metrics."""

import re
from datetime import datetime, timezone

#: Conditions B-Stock states in lot titles, longest first so that
#: "Used - Good Condition" wins over a bare "Used".
_CONDITIONS = (
    "Used - Like New Condition",
    "Used - Good Condition",
    "Used - Fair Condition",
    "Like New Condition",
    "New Condition",
    "Customer Returns",
    "Refurbished",
    "Salvage",
    "Mixed Condition",
    "Shelf Pulls",
    "Overstock",
)

#: Column order for tabular exports.
FIELDS = (
    "auction_id",
    "marketplace",
    "title",
    "url",
    "lot_id",
    "currency",
    "current_bid",
    "msrp",
    "bid_to_msrp_pct",
    "units",
    "cost_per_unit",
    "bids",
    "condition",
    "category",
    "country",
    "location",
    "lot_size",
    "region",
    "offer_type",
    "closes_at",
    "hours_to_close",
    "buyers_premium_pct",
    "buyers_premium_fee",
    "shipping_fee",
    "total_cost_estimate",
    "asins",
    "asin_count",
    "images",
    "image_count",
    "availability",
    "breadcrumbs",
    "product_id",
    "description",
    "scraped_at",
)


def derive_from_title(title):
    """Best-effort structured fields from a lot title.

    Titles follow a loose convention -- "Est. 1 Pallet of <goods>, 11 Units,
    Like New Condition, Ext. Retail $9,690, Salem, OR - West Coast" -- so these
    are hints that fill gaps, never a substitute for the parsed numbers.
    """
    out = {
        "lot_size": None,
        "condition": None,
        "region": None,
        "units": None,
        "country": None,
        "location": None,
    }
    if not title:
        return out

    head = re.split(r"\s+of\s+", title, maxsplit=1)[0]
    head = re.sub(r"^\s*Est\.\s*", "", head).strip()
    if head and len(head) <= 40:
        out["lot_size"] = head

    for condition in _CONDITIONS:
        if condition.lower() in title.lower():
            out["condition"] = condition
            break

    units = re.search(r"([\d,]+)\s+(?:Units|Pieces)\b", title, re.I)
    if units:
        try:
            out["units"] = int(units.group(1).replace(",", ""))
        except ValueError:
            pass

    # US lots end "..., Salem, OR - West Coast"; EU lots end "..., PL Stock".
    region = re.search(
        r",\s*([^,]+?,\s*[A-Z]{2})\s*-\s*(West Coast|East Coast|Central|Midwest)\s*$",
        title,
    )
    if region:
        out["location"] = region.group(1).strip()
        out["region"] = region.group(2)
        out["country"] = "US"
    else:
        bare_region = re.search(r"-\s*(West Coast|East Coast|Central|Midwest)\s*$", title, re.I)
        if bare_region:
            out["region"] = bare_region.group(1)
            out["country"] = "US"
        else:
            stock = re.search(r"\b([A-Z]{2})\s+Stock\s*$", title)
            if stock:
                out["region"] = "{} Stock".format(stock.group(1))
                out["country"] = stock.group(1)

    return out


def build_row(listing, marketplace, detail=None, assets=None, scraped_at=None):
    """Merge listing fields, detail fields and lot assets into one flat row."""
    detail = detail or {}
    assets = assets or {}
    hints = derive_from_title(listing.get("title"))
    now = scraped_at or datetime.now(timezone.utc)

    current_bid = listing.get("current_bid")
    if current_bid is None:
        current_bid = detail.get("detail_bid")
    msrp = listing.get("msrp") or detail.get("retail_price")
    units = listing.get("units") or hints.get("units")

    cost_per_unit = listing.get("cost_per_unit")
    if cost_per_unit is None:
        cost_per_unit = detail.get("detail_cost_per_unit")
    if cost_per_unit is None and current_bid is not None and units:
        cost_per_unit = round(current_bid / units, 4)

    bid_to_msrp_pct = None
    if current_bid is not None and msrp:
        bid_to_msrp_pct = round(current_bid / msrp * 100, 2)

    closes_at = listing.get("closes_at")
    hours_to_close = None
    if closes_at is not None:
        hours_to_close = round((closes_at - now).total_seconds() / 3600.0, 2)

    # What the lot actually costs before freight: bid plus buyer's premium.
    total_cost = None
    if current_bid is not None:
        premium = detail.get("buyers_premium_fee")
        if premium is None and detail.get("buyers_premium_pct") is not None:
            premium = current_bid * detail["buyers_premium_pct"]
        total_cost = round(current_bid + (premium or 0.0), 2)

    asins = assets.get("asins") or []
    images = assets.get("images") or []

    return {
        "auction_id": listing.get("auction_id"),
        "marketplace": marketplace.key,
        "title": listing.get("title") or detail.get("detail_title"),
        "url": listing.get("url"),
        "lot_id": listing.get("lot_id"),
        "currency": listing.get("currency") or detail.get("detail_currency"),
        "current_bid": current_bid,
        "msrp": msrp,
        "bid_to_msrp_pct": bid_to_msrp_pct,
        "units": units,
        "cost_per_unit": cost_per_unit,
        "bids": listing.get("bids"),
        "condition": detail.get("condition") or hints.get("condition"),
        "category": detail.get("category"),
        "country": detail.get("country") or hints.get("country"),
        "location": detail.get("location") or hints.get("location"),
        "lot_size": hints.get("lot_size"),
        "region": hints.get("region"),
        "offer_type": detail.get("offer_type"),
        "closes_at": closes_at.isoformat() if closes_at else None,
        "hours_to_close": hours_to_close,
        "buyers_premium_pct": detail.get("buyers_premium_pct"),
        "buyers_premium_fee": detail.get("buyers_premium_fee"),
        "shipping_fee": detail.get("shipping_fee"),
        "total_cost_estimate": total_cost,
        "asins": asins,
        "asin_count": len(asins),
        "images": images,
        "image_count": len(images),
        "availability": detail.get("availability"),
        "breadcrumbs": detail.get("breadcrumbs"),
        "product_id": listing.get("product_id"),
        "description": detail.get("description"),
        "scraped_at": now.isoformat(),
    }
