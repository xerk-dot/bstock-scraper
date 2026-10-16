"""Run order: walk listing pages, optionally enrich, then build rows."""

import math
from datetime import datetime, timezone

from . import images as images_mod
from . import marketplaces, parse
from .models import build_row

#: Listing page sizes B-Stock's own limiter offers.
PAGE_SIZES = (12, 24, 48)


class Progress:
    """No-op progress sink; the CLI substitutes one that prints to stderr."""

    def page(self, marketplace, number, found, total):
        pass

    def stage(self, message):
        pass

    def warn(self, message):
        pass


def discover_facets(session, marketplace):
    """Fetch a listing page and report the filters currently on offer."""
    page = session.get(marketplace.list_url, params={"limit": PAGE_SIZES[-1]})
    return parse.parse_facets(page, marketplaces.FILTER_PARAMS)


def scrape_marketplace(
    session,
    marketplace,
    filters=None,
    sort=None,
    direction=None,
    page_size=48,
    max_items=None,
    fetch_details=True,
    fetch_images=True,
    image_size="thumb",
    max_images=10,
    progress=None,
):
    """Scrape one marketplace and return ``(rows, meta)``."""
    progress = progress or Progress()
    scraped_at = datetime.now(timezone.utc)

    base_params = dict(filters or {})
    base_params["limit"] = page_size
    if sort:
        base_params["order"] = sort
    if direction:
        base_params["dir"] = direction

    listings = {}
    total_reported = None
    showing_closed = False
    page_number = 1

    while True:
        params = dict(base_params)
        if page_number > 1:
            params["p"] = page_number

        page = session.get(marketplace.list_url, params=params)

        if page_number == 1:
            total_reported = parse.parse_total(page)
            showing_closed = parse.has_no_live_auctions(page)
            if showing_closed:
                progress.warn(
                    "{}: B-Stock reports no live auctions right now; the grid is "
                    "showing recently closed lots.".format(marketplace.key)
                )

        found = 0
        for row in parse.parse_listing(page, marketplace):
            if row["auction_id"] in listings:
                continue
            listings[row["auction_id"]] = row
            found += 1
            if max_items and len(listings) >= max_items:
                break

        progress.page(marketplace.key, page_number, found, total_reported)

        if max_items and len(listings) >= max_items:
            break
        if found == 0:
            break
        if total_reported is not None:
            if page_number >= max(1, math.ceil(total_reported / page_size)):
                break
        page_number += 1

    ordered = list(listings.values())
    if max_items:
        ordered = ordered[:max_items]

    details = {}
    if fetch_details and ordered:
        progress.stage(
            "{}: fetching {} detail page(s)".format(marketplace.key, len(ordered))
        )
        for row in ordered:
            url = row.get("url") or marketplace.auction_url(row["auction_id"])
            try:
                details[row["auction_id"]] = parse.parse_detail(session.get(url))
            except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
                progress.warn(
                    "detail fetch failed for {}: {}".format(row["auction_id"], exc)
                )

    assets = {}
    if fetch_images and ordered:
        lot_ids = [row["lot_id"] for row in ordered if row.get("lot_id")]
        if lot_ids:
            progress.stage(
                "{}: fetching assets for {} lot(s)".format(
                    marketplace.key, len(set(lot_ids))
                )
            )
            assets = images_mod.fetch_lot_assets(
                session,
                marketplace.site_abb,
                lot_ids,
                size=image_size,
                max_images=max_images,
                on_error=lambda exc: progress.warn("asset lookup failed: {}".format(exc)),
            )

    rows = [
        build_row(
            row,
            marketplace,
            detail=details.get(row["auction_id"]),
            assets=assets.get((row.get("lot_id") or "").lower()),
            scraped_at=scraped_at,
        )
        for row in ordered
    ]

    meta = {
        "marketplace": marketplace.key,
        "label": marketplace.label,
        "total_reported": total_reported,
        "returned": len(rows),
        "showing_closed_auctions": showing_closed,
        "pages_fetched": page_number,
    }
    return rows, meta
