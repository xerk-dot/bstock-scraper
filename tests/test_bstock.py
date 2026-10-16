"""Offline tests. Fixtures are real (scrubbed) pages, so no network is used."""

import gzip
import io
import os
import unittest
from datetime import datetime, timedelta, timezone

from bstock import export, http, images, marketplaces, models, parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with gzip.open(os.path.join(FIXTURES, name), "rt", encoding="utf-8") as handle:
        return handle.read()


class MoneyTests(unittest.TestCase):
    def test_symbols_and_separators(self):
        cases = [
            ("$9.09", 9.09, "USD"),
            ("€5,825.00", 5825.0, "EUR"),
            ("€11,100", 11100.0, "EUR"),
            ("£1,234", 1234.0, "GBP"),
            ("$46,150.05", 46150.05, "USD"),
            ("1.234,56 €", 1234.56, "EUR"),
        ]
        for raw, amount, currency in cases:
            with self.subTest(raw=raw):
                got_amount, got_currency = parse.parse_money(raw)
                self.assertAlmostEqual(got_amount, amount, places=2)
                self.assertEqual(got_currency, currency)

    def test_missing_and_malformed(self):
        for raw in ("", None, "n/a", "--"):
            self.assertEqual(parse.parse_money(raw)[0], None)

    def test_parse_int_strips_separators(self):
        self.assertEqual(parse.parse_int("3,007 Pieces"), 3007)
        self.assertEqual(parse.parse_int("Bids: 17"), 17)
        self.assertIsNone(parse.parse_int("none"))


class EndTimeTests(unittest.TestCase):
    def test_rfc2822_with_offset(self):
        parsed = parse.parse_end_time("Sat, 17 Oct 2026 13:35:00 +0100")
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.hour, 13)
        self.assertIsNotNone(parsed.tzinfo)

    def test_garbage_is_none(self):
        self.assertIsNone(parse.parse_end_time("soon"))
        self.assertIsNone(parse.parse_end_time(""))


class ListingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = fixture("eu_listing.html.gz")
        cls.rows = parse.parse_listing(cls.page, marketplaces.EU)

    def test_parses_every_tile(self):
        self.assertEqual(parse.parse_total(self.page), 31)
        self.assertEqual(len(self.rows), 31)

    def test_ids_are_unique(self):
        ids = [row["auction_id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_currency_comes_from_the_rendered_symbol(self):
        # The GTM dataLayer reports GBP for every EU tile, but the sidebar
        # facets say 28 EUR / 3 GBP. The rendered symbol must win.
        counts = {}
        for row in self.rows:
            counts[row["currency"]] = counts.get(row["currency"], 0) + 1
        self.assertEqual(counts, {"EUR": 28, "GBP": 3})

    def test_known_tile_fields(self):
        row = next(r for r in self.rows if r["auction_id"] == 58315)
        self.assertEqual(row["lot_id"], "a2z_cr_sk_20261015_mixed_047")
        self.assertEqual(row["current_bid"], 11100.0)
        self.assertEqual(row["cost_per_unit"], 3.69)
        self.assertEqual(row["bids"], 17)
        self.assertEqual(row["units"], 3007)
        self.assertAlmostEqual(row["msrp"], 97475.68, places=2)
        self.assertEqual(row["currency"], "EUR")
        self.assertIn("auction/view/id/58315", row["url"])
        self.assertIsNotNone(row["closes_at"])

    def test_required_fields_present_on_every_row(self):
        for row in self.rows:
            with self.subTest(auction=row["auction_id"]):
                for field in ("auction_id", "url", "title", "current_bid", "currency"):
                    self.assertIsNotNone(row[field])

    def test_live_listing_is_not_flagged_as_closed(self):
        self.assertFalse(parse.has_no_live_auctions(self.page))


class ClosedListingTests(unittest.TestCase):
    """The US storefront falls back to closed lots when nothing is live."""

    @classmethod
    def setUpClass(cls):
        cls.page = fixture("us_listing_closed.html.gz")
        cls.rows = parse.parse_listing(cls.page, marketplaces.US)

    def test_detects_the_no_live_auctions_state(self):
        self.assertTrue(parse.has_no_live_auctions(self.page))

    def test_still_parses_the_closed_tiles(self):
        self.assertEqual(len(self.rows), 4)
        row = next(r for r in self.rows if r["auction_id"] == 40697)
        self.assertEqual(row["current_bid"], 100.0)
        self.assertEqual(row["units"], 11)
        self.assertEqual(row["currency"], "USD")
        self.assertAlmostEqual(row["msrp"], 9689.89, places=2)
        # Closed tiles carry no lot SKU, so assets cannot be looked up.
        self.assertIsNone(row["lot_id"])


class FacetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facets = parse.parse_facets(
            fixture("eu_listing.html.gz"), marketplaces.FILTER_PARAMS
        )

    def test_discovers_each_filter_group(self):
        for param in ("condition", "product_type", "shipment_type", "country", "currency"):
            self.assertIn(param, self.facets)

    def test_country_values_and_counts(self):
        countries = {f["value"]: f["count"] for f in self.facets["country"]}
        self.assertEqual(countries, {"IT": 2, "PL": 12, "SK": 13, "ES": 1, "GB": 3})

    def test_labels_have_counts_stripped(self):
        labels = {f["value"]: f["label"] for f in self.facets["country"]}
        self.assertEqual(labels["PL"], "Poland")

    def test_login_links_are_not_mistaken_for_facets(self):
        # The SSO link carries a client_id parameter; it must not appear.
        self.assertNotIn("client_id", self.facets)


class DetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detail = parse.parse_detail(fixture("eu_detail.html.gz"))

    def test_jsonld_fields(self):
        self.assertEqual(self.detail["condition"], "Customer Returns")
        self.assertEqual(self.detail["country"], "ES")
        self.assertEqual(self.detail["location"], "Cabanillas del Campo, Madrid, ES")
        self.assertEqual(self.detail["detail_currency"], "EUR")
        self.assertEqual(self.detail["detail_bid"], 5825.0)
        self.assertEqual(self.detail["availability"], "InStock")

    def test_title_has_seo_suffix_removed(self):
        self.assertEqual(
            self.detail["detail_title"],
            "UNMANIFESTED Truckload of Mixed Categories, Customer Returns, ES Stock",
        )

    def test_breadcrumbs_are_not_duplicated(self):
        self.assertEqual(self.detail["category"], "Mixed Lots")
        self.assertEqual(self.detail["breadcrumbs"], "Home > Mixed Lots")

    def test_offer_type_from_fixed_price_flag(self):
        self.assertEqual(self.detail["offer_type"], "Auction")

    def test_fees(self):
        self.assertAlmostEqual(self.detail["buyers_premium_pct"], 0.05)
        self.assertAlmostEqual(self.detail["buyers_premium_fee"], 291.25)

    def test_description_entities_fully_unescaped(self):
        self.assertIn("€", self.detail["description"])
        self.assertNotIn("&euro;", self.detail["description"])
        self.assertNotIn("&amp;", self.detail["description"])


class TitleDerivationTests(unittest.TestCase):
    def test_us_title(self):
        got = models.derive_from_title(
            "Est. 1 Pallet of Dell XPS 8960 Gaming Desktop Computers & More, "
            "11 Units, Like New Condition, Ext. Retail $9,690, Salem, OR - West Coast"
        )
        self.assertEqual(got["lot_size"], "1 Pallet")
        self.assertEqual(got["condition"], "Like New Condition")
        self.assertEqual(got["region"], "West Coast")
        self.assertEqual(got["country"], "US")
        self.assertEqual(got["location"], "Salem, OR")
        self.assertEqual(got["units"], 11)

    def test_eu_title(self):
        got = models.derive_from_title(
            "Small Truckload of Sporting Goods, Toys & More, 3,007 Pieces, "
            "Customer Returns, Total Retail €97,476, DE Stock"
        )
        self.assertEqual(got["lot_size"], "Small Truckload")
        self.assertEqual(got["condition"], "Customer Returns")
        self.assertEqual(got["country"], "DE")
        self.assertEqual(got["units"], 3007)

    def test_longest_condition_wins(self):
        got = models.derive_from_title("1 Pallet of Tools, Used - Good Condition")
        self.assertEqual(got["condition"], "Used - Good Condition")

    def test_empty_title(self):
        self.assertEqual(models.derive_from_title("")["lot_size"], None)


class RowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        self.listing = {
            "auction_id": 1,
            "url": "https://example.test/1",
            "title": "2 Pallets of Tools, 100 Units, Customer Returns, PL Stock",
            "lot_id": "a2z_x",
            "current_bid": 500.0,
            "cost_per_unit": None,
            "bids": 3,
            "closes_at": self.now + timedelta(hours=6),
            "currency": "EUR",
            "msrp": 5000.0,
            "units": 100,
            "product_id": "42",
        }

    def build(self, detail=None, assets=None):
        return models.build_row(
            self.listing, marketplaces.EU,
            detail=detail, assets=assets, scraped_at=self.now,
        )

    def test_bid_to_msrp_ratio(self):
        self.assertEqual(self.build()["bid_to_msrp_pct"], 10.0)

    def test_cost_per_unit_is_computed_when_absent(self):
        self.assertEqual(self.build()["cost_per_unit"], 5.0)

    def test_site_cost_per_unit_is_preferred(self):
        self.listing["cost_per_unit"] = 4.75
        self.assertEqual(self.build()["cost_per_unit"], 4.75)

    def test_hours_to_close(self):
        self.assertEqual(self.build()["hours_to_close"], 6.0)

    def test_total_cost_adds_buyers_premium(self):
        row = self.build(detail={"buyers_premium_fee": 25.0})
        self.assertEqual(row["total_cost_estimate"], 525.0)

    def test_total_cost_falls_back_to_premium_percentage(self):
        row = self.build(detail={"buyers_premium_pct": 0.05})
        self.assertEqual(row["total_cost_estimate"], 525.0)

    def test_detail_country_beats_title_hint(self):
        self.assertEqual(self.build()["country"], "PL")
        self.assertEqual(self.build(detail={"country": "SK"})["country"], "SK")

    def test_assets_are_counted(self):
        row = self.build(assets={"asins": ["B1", "B2"], "images": ["u1"]})
        self.assertEqual(row["asin_count"], 2)
        self.assertEqual(row["image_count"], 1)

    def test_no_msrp_leaves_ratio_empty(self):
        self.listing["msrp"] = None
        self.assertIsNone(self.build()["bid_to_msrp_pct"])

    def test_row_covers_the_declared_schema(self):
        self.assertEqual(set(self.build()), set(models.FIELDS))


class AssetTests(unittest.TestCase):
    def test_lot_ids_are_matched_case_insensitively(self):
        payload = {
            "results": [
                {
                    "lot_id": "A2Z_CR_SK_1",
                    "resource_type": "asin",
                    "resource_id": "B0001",
                    "url": "https://example.test/orig.jpg",
                    "images": [{"name": "thumb", "url": "https://example.test/t.jpg"}],
                }
            ]
        }
        assets = {}
        images._collect(payload, assets, "thumb", 10)
        self.assertIn("a2z_cr_sk_1", assets)
        self.assertEqual(assets["a2z_cr_sk_1"]["asins"], ["B0001"])
        self.assertEqual(assets["a2z_cr_sk_1"]["images"], ["https://example.test/t.jpg"])

    def test_max_images_is_respected_but_asins_still_collected(self):
        results = []
        for index in range(5):
            results.append({
                "lot_id": "LOT",
                "resource_type": "asin",
                "resource_id": "B{}".format(index),
                "images": [{"name": "thumb", "url": "https://example.test/{}.jpg".format(index)}],
            })
        assets = {}
        images._collect({"results": results}, assets, "thumb", 2)
        self.assertEqual(len(assets["lot"]["images"]), 2)
        self.assertEqual(len(assets["lot"]["asins"]), 5)

    def test_falls_back_to_original_when_rendition_missing(self):
        payload = {"results": [{"lot_id": "L", "url": "https://example.test/o.jpg"}]}
        assets = {}
        images._collect(payload, assets, "thumb", 10)
        self.assertEqual(assets["l"]["images"], ["https://example.test/o.jpg"])

    def test_empty_lot_list_makes_no_request(self):
        def explode(*_args, **_kwargs):
            raise AssertionError("should not have made a request")

        session = type("S", (), {"get": explode})()
        self.assertEqual(images.fetch_lot_assets(session, "a2z", []), {})


class ChallengeTests(unittest.TestCase):
    def test_cloudflare_header_is_detected(self):
        with self.assertRaises(http.BlockedError):
            http._guard_challenge(403, {"Cf-Mitigated": "challenge"}, "", "u")

    def test_interstitial_body_is_detected(self):
        with self.assertRaises(http.BlockedError):
            http._guard_challenge(403, {}, "<title>Just a moment...</title>", "u")

    def test_normal_response_passes(self):
        http._guard_challenge(200, {}, "<html>fine</html>", "u")


class ExportTests(unittest.TestCase):
    rows = [{
        "auction_id": 1,
        "asins": ["B1", "B2"],
        "title": "Lot",
        "current_bid": None,
    }]

    def test_csv_flattens_lists_and_blanks_none(self):
        buffer = io.StringIO()
        export.write(self.rows, fmt="csv", stream=buffer,
                     fields=("auction_id", "asins", "current_bid"))
        lines = buffer.getvalue().strip().splitlines()
        self.assertEqual(lines[0], "auction_id,asins,current_bid")
        self.assertEqual(lines[1], '1,B1; B2,')

    def test_jsonl_is_one_object_per_line(self):
        buffer = io.StringIO()
        export.write(self.rows * 3, fmt="jsonl", stream=buffer)
        self.assertEqual(len(buffer.getvalue().strip().splitlines()), 3)

    def test_table_handles_empty_input(self):
        buffer = io.StringIO()
        export.write([], fmt="table", stream=buffer)
        self.assertIn("no lots", buffer.getvalue())

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            export.write(self.rows, fmt="xml", stream=io.StringIO())


class MarketplaceTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(
            marketplaces.US.list_url,
            "https://bstock.com/amazon/auction/auction/list/",
        )
        self.assertEqual(
            marketplaces.EU.auction_url(5),
            "https://bstock.com/amazoneu/auction/auction/view/id/5/",
        )

    def test_unknown_marketplace_lists_options(self):
        with self.assertRaises(KeyError) as caught:
            marketplaces.get("apac")
        self.assertIn("eu", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
