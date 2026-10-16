"""Client for B-Stock's public lot asset service.

The listing and detail pages ship an image placeholder and let JavaScript fill
it in from ``bfile-prod.appspot.com``. That service is a plain read-only JSON
endpoint, and besides image URLs it returns the **Amazon ASIN** behind each
picture -- the most interesting field this scraper surfaces.
"""

IMAGE_SERVICE = "https://bfile-prod.appspot.com"

#: Lots per request. The service accepts a comma-joined list; batching keeps the
#: number of round trips (and therefore the load we place on it) low.
BATCH_SIZE = 20


def fetch_lot_assets(
    session,
    site_abb,
    lot_ids,
    size="thumb",
    max_images=10,
    batch_size=BATCH_SIZE,
    on_error=None,
):
    """Look up images and ASINs for each lot id.

    Args:
        session: A :class:`bstock.http.Session`.
        site_abb: Marketplace abbreviation (``amz`` for US, ``a2z`` for EU).
        lot_ids: Lot SKUs taken from the listing tiles.
        size: Image rendition name, e.g. ``thumb``. ``original`` asks for the
            unresized upload instead of a rendition.
        max_images: Cap on images recorded per lot.
        batch_size: Lots per request.
        on_error: Optional callback invoked with an exception per failed batch;
            a failure degrades that batch to empty rather than aborting a run.

    Returns:
        ``{lot_id_lowercased: {"asins": [...], "images": [...]}}``
    """
    wanted = [lot for lot in dict.fromkeys(lot_ids) if lot]
    assets = {}
    if not wanted:
        return assets

    for start in range(0, len(wanted), max(1, batch_size)):
        batch = wanted[start:start + max(1, batch_size)]
        params = {
            "marketplace": site_abb,
            "lot": ",".join(batch),
            "relationship": "featured",
            "images_name": size,
        }
        if max_images:
            params["limit_img_per_lot"] = max_images
        try:
            payload = session.get(
                IMAGE_SERVICE + "/lots/search", params=params, as_json=True
            )
        except Exception as exc:  # noqa: BLE001 - assets are best-effort
            if on_error is not None:
                on_error(exc)
            continue
        _collect(payload, assets, size, max_images)

    return assets


def _collect(payload, assets, size, max_images):
    results = (payload or {}).get("results") or []
    for item in results:
        # The service echoes lot ids uppercased; the page emits them lowercased.
        lot_id = (item.get("lot_id") or "").lower()
        if not lot_id:
            continue
        bucket = assets.setdefault(lot_id, {"asins": [], "images": []})

        if item.get("resource_type") == "asin":
            asin = item.get("resource_id")
            if asin and asin not in bucket["asins"]:
                bucket["asins"].append(asin)

        if max_images and len(bucket["images"]) >= max_images:
            continue

        url = _pick_image(item, size)
        if url and url not in bucket["images"]:
            bucket["images"].append(url)


def _pick_image(item, size):
    """Choose the requested rendition, falling back to the original upload."""
    for rendition in item.get("images") or []:
        if rendition.get("name") == size and rendition.get("url"):
            return rendition["url"]
    for rendition in item.get("images") or []:
        if rendition.get("url"):
            return rendition["url"]
    return item.get("url")
