# bstock-scraper

A zero-dependency Python CLI that scrapes public **B-Stock Amazon liquidation
auction** listings from the US and EU marketplaces and writes them to CSV, JSON,
JSON Lines, or a terminal table.

One row per lot: current bid, MSRP, bid-to-MSRP ratio, units, cost per unit, bid
count, condition, category, country, close time, buyer's-premium fees — and the
**Amazon ASINs** and product image URLs behind each lot.

It can also **rank** lots for a specific buyer: how far the freight has to
travel to you, how cheap the lot is against retail, how big a pallet you want,
and how contested the bidding is.

```console
$ bstock --marketplace eu --filter country=PL --max-items 4 --sort end_time --dir asc --format table
auction_id  currency  current_bid  msrp      bid_to_msrp_pct  units  cost_per_unit  bids  country  title
-------------------------------------------------------------------------------------------------------
58241       EUR       2320.0       25832.4   8.98             464    5.0                  PL       Truckload of Pet Products, Furniture & More, 464 Pieces…
58026       EUR       5075.0                                         5075.0         5     PL       UNMANIFESTED Truckload of Mixed Categories, Customer Re…
58023       EUR       2075.0       52311.68  3.97             792    2.62           9     PL       Truckload of Home Improvement, Auto Goods & More, 792 P…
58020       EUR       3225.0       55651.52  5.79             1300   2.48           10    PL       Small Truckload of Home Improvement, Auto Goods & More,…
```

## Install

Python 3.8+ and **no third-party packages** — the whole thing runs on the
standard library, so there is nothing to resolve and nothing to audit.

```bash
git clone https://github.com/xerk-dot/bstock-scraper
cd bstock-scraper
python3 -m bstock --help          # run straight from the clone
pip install -e .                  # optional: puts `bstock` on your PATH
```

## Quickstart

```bash
# See which filters the marketplace offers right now, with live lot counts
bstock --marketplace eu --list-filters

# What's live in the EU marketplace, closing soonest first
bstock -m eu -n 20 --sort end_time --dir asc -f table

# Polish customer-return lots to CSV, listing data only (one request per page)
bstock -m eu --filter country=PL --no-details --no-images -o lots.csv

# Both marketplaces, full enrichment, newline-delimited JSON
bstock -m us,eu -n 100 -f jsonl -o lots.jsonl

# Only the columns you care about
bstock -m eu --fields auction_id,current_bid,msrp,bid_to_msrp_pct,asins -o cheap.csv

# Rank for a buyer in Chicago who wants small pallets
bstock -m us --rank --home "Chicago, IL" --format table
```

`--list-filters` is the one to run first. B-Stock assigns its filter ids
per-marketplace, so instead of hardcoding magic numbers this reads the live
sidebar and prints exactly what the marketplace accepts today:

```console
$ bstock -m eu --list-filters

Amazon Europe Online Liquidation Auctions [eu]
  condition
    --filter condition=8          Customer Returns (31 lots)
  product_type
    --filter product_type=103        Full Truckload (7 lots)
    --filter product_type=101        LTL (18 lots)
    --filter product_type=102        Small Truckload (6 lots)
  country
    --filter country=PL         Poland (12 lots)
    --filter country=SK         Slovakia (13 lots)
    --filter country=GB         United Kingdom (3 lots)
  currency
    --filter currency=EUR        Euro (28 lots)
    --filter currency=GBP        British Pound Sterling (3 lots)
```

## Marketplaces

| Key  | Marketplace                                  | Currencies |
| ---- | -------------------------------------------- | ---------- |
| `us` | Amazon Liquidation Auctions                  | USD        |
| `eu` | Amazon Europe Online Liquidation Auctions    | EUR, GBP   |

When a storefront has nothing live, B-Stock shows recently *closed* lots
instead. The scraper detects that state and warns you, so an unexpectedly small
result is never silently mistaken for a working run:

```
! us: B-Stock reports no live auctions right now; the grid is showing recently closed lots.
```

## Output schema

One flat row per lot. `--fields` selects a subset; `csv` and `table` join list
columns with `; `.

| Column | Source | Notes |
| --- | --- | --- |
| `auction_id` | listing | B-Stock's numeric auction id |
| `marketplace` | — | `us` or `eu` |
| `title`, `url` | listing | |
| `lot_id` | listing | Lot SKU, e.g. `a2z_cr_pl_20261014_bx3pl_050` |
| `currency` | listing | From the rendered price symbol (see note below) |
| `current_bid` | listing | Buy Now lots show the fixed price |
| `msrp` | listing | Extended/total retail |
| `bid_to_msrp_pct` | derived | `current_bid / msrp × 100` |
| `units` | listing | Null on unmanifested lots |
| `cost_per_unit` | listing | Site value, else `current_bid / units` |
| `bids` | listing | Null on Buy Now lots |
| `condition` | detail | e.g. `Customer Returns`, `Like New Condition` |
| `category`, `breadcrumbs` | detail | From the breadcrumb trail |
| `country`, `location` | detail | Falls back to the title's `PL Stock` / `Salem, OR` |
| `lot_size`, `region` | derived | e.g. `2 Pallets`, `Small Truckload`, `West Coast` |
| `offer_type` | detail | `Auction` or `Buy Now` |
| `closes_at`, `hours_to_close` | listing | ISO 8601 with offset |
| `buyers_premium_pct`, `buyers_premium_fee`, `shipping_fee` | detail | |
| `total_cost_estimate` | derived | Bid plus buyer's premium, before freight |
| `asins`, `asin_count` | assets | Amazon ASINs in the lot |
| `images`, `image_count` | assets | Product image URLs |
| `availability` | detail | schema.org availability |
| `product_id` | listing | Internal Magento product id |
| `description` | detail | Full lot description |
| `scraped_at` | — | UTC timestamp of the run |

With `--rank`, ten more columns are appended:

| Column | Notes |
| --- | --- |
| `rank` | 1 = best; unscoreable lots sort last |
| `score` | 0–100 composite |
| `distance_km` | Great-circle km from `--home` |
| `geo_precision` | `exact`, `metro`, `state` or `country` |
| `size_units` | Lot size in pallet-equivalents |
| `score_proximity`, `score_value`, `score_size`, `score_competition` | Per-axis 0–1, blank if the axis was dropped |
| `score_basis` | Which axes the score actually used |

Detail-page columns need `--no-details` *off* (the default); asset columns need
`--no-images` off. Both flags make runs dramatically faster when you only want
listing economics.

## Ranking

`--rank` scores every lot and sorts best-first. Tell it where the freight is
going with `--home` and it will weigh distance alongside price:

```console
$ bstock -m us --rank --home "Chicago, IL" --format table
rank  score  distance_km  lot_size   current_bid  bid_to_msrp_pct  units  cost_per_unit  bids  location
1     97.43  200.8        1 Pallet   100.0        0.22             2984   0.03           0     Caledonia, MI, US
2     93.82  200.8        2 Pallets  100.0        0.26             1536   0.07           0     Caledonia, MI, US
3     86.82  785.8        2 Pallets  100.0        0.23             2205   0.05           0     Chattanooga, TN, US
4     69.23  2857.5       1 Pallet   100.0        1.03             11     9.09           0     Salem, OR, US
```

Note rank 4: a single pallet, but 2,857 km away. Freight sinks it below the
two-pallet lots three hours up the road.

### The four axes

Each is scored 0–1 against an absolute, documented threshold — not against the
rest of the batch — so a score means the same thing from one run to the next.

| Axis | Measures | Scores 1.0 at | Scores 0.0 at | Default weight |
| --- | --- | --- | --- | --- |
| `proximity` | km from `--home` | same city | `--max-distance` (2500 km) | 0.30 |
| `value` | `bid_to_msrp_pct` | 0% of retail | `--msrp-cap` (40%) | 0.30 |
| `size` | pallet-equivalents | 1 pallet | `--size-cap` (8 pallets) | 0.25 |
| `competition` | bid count | no bids yet | `--bids-cap` (15 bids) | 0.15 |

Lot size is normalised to pallet-equivalents so mixed listings compare:
`1 Pallet` → 1, `LTL` → 4, `Small Truckload` → 12, `Truckload` → 24,
`Full Truckload` → 26. `--prefer-size large` inverts the axis and
`--prefer-size any` removes it.

Re-weight freely — weights are renormalised, so they need not sum to 1:

```bash
# freight dominates; I'll take whatever is close
bstock -m us --rank --home "Chicago, IL" --weight proximity=0.6 --weight value=0.2

# ignore size, hunt purely on price
bstock -m us --rank --home "Chicago, IL" --prefer-size any --weight value=0.8

# only consider lots within a day's drive
bstock -m us --rank --home "Chicago, IL" --max-distance 600 --top 10
```

### Reading a score

Every component is written back onto the row, so a ranking can always be taken
apart: `score_proximity`, `score_value`, `score_size`, `score_competition`,
plus `distance_km`, `size_units` and `score_basis`.

**Missing data is dropped, never zeroed.** Unmanifested lots have no MSRP, and
Buy Now lots have no bid count. Rather than scoring those axes 0 — which would
punish a lot for a field B-Stock never published — the axis is removed and the
remaining weights are renormalised. `score_basis` records which axes were
actually used, so `proximity+size+value` tells you the value axis was absent.

### How locations are resolved

`--home` accepts a US metro (`"Chicago, IL"`), a state code (`"IL"`), a country
code (`"GB"`), or literal coordinates (`"41.88,-87.63"`).

Distances are great-circle, from a three-tier lookup, and `geo_precision` on
every row tells you which tier was used:

- `metro` — the warehouse towns that recur in B-Stock titles (±a few km)
- `state` — a US state centroid, for an unrecognised city (can be 300 km off)
- `country` — a country centroid, which is what EU lots resolve to

It is a freight *proxy*, not a routing engine: no roads, no carrier rates, no
accessorials. Use it to sort candidates, then price the real freight.

### A note for US buyers

The US marketplace is frequently empty between inventory drops, and B-Stock
then shows recently *closed* lots instead (the scraper warns when this
happens). The EU marketplace is usually the one with live inventory — but
ranking it from a US address is mostly academic, since every EU lot sits
~7,000 km away and scores accordingly.

## How it works

Everything here was derived by reading the public HTML the site serves to an
ordinary HTTP client. There are four sources:

1. **Listing pages** — `/<market>/auction/auction/list/`, paged with
   `?p=N&limit=12|24|48` and sorted with `?order=end_time|start_time|current_bid_amt|number_bids|name&dir=asc|desc`.
   Each lot is an `<li id="auction-ID">` tile holding the bid, cost per unit, bid
   count, a `data-end-time` close timestamp, and the lot SKU in the tile's
   `product-image-<sku>` class.
2. **A per-tile analytics payload** — each tile emits a Google Tag Manager
   `select_item` event carrying extended retail and unit count as *numbers*,
   which beats re-parsing formatted text.
3. **Detail pages** — `/auction/auction/view/id/<id>/` embed a schema.org
   JSON-LD `Product`/`Offer` block (condition, location, country, currency) plus
   hidden inputs for the fee breakdown and the `isFixedPrice` flag that
   distinguishes Buy Now from auction lots.
4. **The lot asset service** — the pages ship an image placeholder and let
   JavaScript fill it from `bfile-prod.appspot.com/lots/search`. That endpoint is
   plain read-only JSON, and alongside image URLs it returns the **ASIN** behind
   each picture. Lookups are batched 20 lots per request.

A few traps worth knowing if you fork this:

- **The analytics `currency` field lies.** It reports the marketplace default
  for every tile — all 31 EU lots claim `GBP` even though the sidebar facets say
  28 are EUR. The rendered price symbol is authoritative. There is a regression
  test pinning the parse to the site's own facet counts.
- **`item_category` is always `"Default Category"`** in the analytics payload.
  Real categories come from the detail-page breadcrumbs.
- **The JSON-LD is not valid strict JSON** — B-Stock emits literal newlines
  inside strings, so it is decoded with `strict=False`.
- **Entities are double-escaped** in the JSON-LD description (`&amp;euro;`).
- **Breadcrumbs render their label twice** (a hidden microdata span plus the
  visible text), so the parser reads `itemprop="name"` rather than the text.

## Scope and conduct

This tool is deliberately limited, and the limits are the point:

- **Read-only.** It performs `GET` requests only. It never authenticates, and it
  never touches `/auction/auction/ajax/` — the endpoint that places bids.
- **No bot-protection bypass.** B-Stock fronts its newer `/buy/` pages with a
  Cloudflare managed challenge. If a challenge is served, the scraper raises
  `BlockedError` and explains itself rather than trying to defeat it. The legacy
  marketplace paths it does use serve normally to a plain client.
- **robots.txt is respected** by default. B-Stock's `robots.txt` disallows admin,
  skin, and `?mode=list` paths; the auction listing and detail pages this reads
  are permitted. `--ignore-robots` exists, but using it is your decision and
  your responsibility.
- **Rate limited.** One request per second by default (`--delay`), with
  exponential backoff that honours `Retry-After` on 429s and 5xx.
- **No personal data.** Bidder identities are anonymous on B-Stock and bid
  history requires a login, so none is collected. Don't add it.

## Development

```bash
python3 -m unittest discover -s tests -v
```

86 tests, no network: they run against gzipped captures of real listing and
detail pages stored in `tests/fixtures/` (scrubbed of session artifacts). CI runs
them on Python 3.8 / 3.10 / 3.12 / 3.13.

```
bstock/
  cli.py           argparse front end
  scraper.py       pagination, enrichment, run order
  parse.py         listing, facet, and detail-page parsing
  models.py        flat row schema and derived metrics
  ranking.py       buyer-specific scoring and sorting
  geo.py           metro/state/country coordinates and distance
  images.py        lot asset (image + ASIN) service client
  http.py          polite stdlib session: rate limit, retries, robots, challenge guard
  marketplaces.py  marketplace definitions, sort fields, filter params
  export.py        csv / json / jsonl / table writers
```

Site markup drifts. If a column starts coming back empty, the parser's hooks are
all in `parse.py` and each is anchored on a named id, class, or embedded data
blob — re-capture a fixture and the failing test will point at the change.

## Disclaimer

Independent and unofficial. Not affiliated with, endorsed by, or sponsored by
B-Stock Solutions, Inc. or Amazon. It collects only publicly available listing
data. You are responsible for your own use of it, including compliance with
B-Stock's terms of service and any applicable law. Provided under the MIT
license with no warranty.

## License

[MIT](LICENSE)
