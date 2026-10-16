"""Offline tests. Fixtures are real (scrubbed) pages, so no network is used."""

import gzip
import io
import os
import unittest
from datetime import datetime, timedelta, timezone

from bstock import export, geo, http, images, marketplaces, models, parse, ranking

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


class GeoTests(unittest.TestCase):
    CHICAGO = (41.88, -87.63)

    def test_haversine_against_a_known_distance(self):
        # Chicago -> Los Angeles is about 2800 km.
        distance = geo.haversine_km(self.CHICAGO, (34.05, -118.24))
        self.assertAlmostEqual(distance, 2805, delta=40)

    def test_zero_distance(self):
        self.assertAlmostEqual(geo.haversine_km(self.CHICAGO, self.CHICAGO), 0.0)

    def test_city_state_resolves_to_metro(self):
        point, precision = geo.lookup_place("Chicago, IL")
        self.assertEqual(precision, "metro")
        self.assertAlmostEqual(point[0], 41.88, places=2)

    def test_country_suffix_does_not_mask_the_state(self):
        # Regression: detail pages emit "Caledonia, MI, US". Reading only the
        # last token resolved every US lot to the national centroid, so every
        # lot came back the same distance from home.
        point, precision = geo.lookup_place("Caledonia, MI, US")
        self.assertEqual(precision, "metro")
        self.assertLess(geo.haversine_km(self.CHICAGO, point), 300)

    def test_unknown_city_falls_back_to_its_state(self):
        point, precision = geo.lookup_place("Nowheresville, OH, US")
        self.assertEqual(precision, "state")
        self.assertEqual(point, geo.STATE_CENTROIDS["OH"])

    def test_eu_location_resolves_to_country(self):
        point, precision = geo.lookup_place("Cabanillas del Campo, Madrid, ES")
        self.assertEqual(precision, "country")
        self.assertEqual(point, geo.COUNTRY_CENTROIDS["ES"])

    def test_bare_codes(self):
        self.assertEqual(geo.lookup_place("IL")[1], "state")
        self.assertEqual(geo.lookup_place("PL")[1], "country")

    def test_literal_coordinates(self):
        point, precision = geo.lookup_place("41.88,-87.63")
        self.assertEqual(precision, "exact")
        self.assertEqual(point, (41.88, -87.63))

    def test_out_of_range_coordinates_are_not_treated_as_a_point(self):
        self.assertEqual(geo.lookup_place("999,999"), (None, None))

    def test_unresolvable(self):
        self.assertEqual(geo.lookup_place(""), (None, None))
        self.assertEqual(geo.lookup_place("Atlantis"), (None, None))

    def test_locate_row_prefers_location_then_country(self):
        self.assertEqual(
            geo.locate_row({"location": "Chicago, IL", "country": "PL"})[1], "metro")
        self.assertEqual(
            geo.locate_row({"location": None, "country": "PL"})[1], "country")
        self.assertEqual(geo.locate_row({}), (None, None))


class SizeUnitTests(unittest.TestCase):
    def test_pallet_counts(self):
        self.assertEqual(ranking.size_units("1 Pallet"), 1.0)
        self.assertEqual(ranking.size_units("4 Pallets"), 4.0)

    def test_truckload_tiers_are_ordered(self):
        small = ranking.size_units("Small Truckload")
        full = ranking.size_units("Full Truckload")
        plain = ranking.size_units("Truckload")
        self.assertLess(small, plain)
        self.assertLess(plain, full)

    def test_unmanifested_truckload_is_a_truckload(self):
        self.assertEqual(ranking.size_units("UNMANIFESTED Truckload"), 24.0)

    def test_ltl_sits_between_pallets_and_truckloads(self):
        self.assertEqual(ranking.size_units("LTL"), 4.0)

    def test_unknown_and_empty(self):
        self.assertIsNone(ranking.size_units("Mystery Box"))
        self.assertIsNone(ranking.size_units(None))


class ScoringTests(unittest.TestCase):
    CHICAGO = (41.88, -87.63)

    def row(self, **overrides):
        base = {
            "lot_size": "1 Pallet",
            "bid_to_msrp_pct": 0.0,
            "bids": 0,
            "location": "Chicago, IL",
            "country": "US",
        }
        base.update(overrides)
        return base

    def test_a_perfect_lot_scores_100(self):
        scored = ranking.score_row(self.row(), home=self.CHICAGO)
        self.assertEqual(scored["score"], 100.0)
        self.assertEqual(scored["geo_precision"], "metro")

    def test_each_axis_moves_the_score_down(self):
        best = ranking.score_row(self.row(), home=self.CHICAGO)["score"]
        for field, value in [
            ("bid_to_msrp_pct", 40.0),
            ("bids", 15),
            ("lot_size", "Full Truckload"),
            ("location", "Salem, OR"),
        ]:
            with self.subTest(field=field):
                worse = ranking.score_row(
                    self.row(**{field: value}), home=self.CHICAGO)["score"]
                self.assertLess(worse, best)

    def test_a_terrible_lot_stays_within_bounds(self):
        scored = ranking.score_row(
            self.row(bid_to_msrp_pct=500.0, bids=900, lot_size="Full Truckload",
                     location="Salem, OR"),
            home=self.CHICAGO,
        )
        # Value, size and competition clamp to zero; proximity decays but never
        # reaches it, so the composite is small yet strictly positive.
        self.assertEqual(scored["score_value"], 0.0)
        self.assertEqual(scored["score_size"], 0.0)
        self.assertEqual(scored["score_competition"], 0.0)
        self.assertGreater(scored["score"], 0.0)
        self.assertLess(scored["score"], 10.0)

    def test_missing_axes_are_dropped_not_zeroed(self):
        # An unmanifested lot has no MSRP; that must not read as "worst value".
        scored = ranking.score_row(
            self.row(bid_to_msrp_pct=None), home=self.CHICAGO)
        self.assertIsNone(scored["score_value"])
        self.assertNotIn("value", scored["score_basis"])
        self.assertEqual(scored["score"], 100.0)

    def test_no_home_drops_the_proximity_axis(self):
        scored = ranking.score_row(self.row())
        self.assertIsNone(scored["score_proximity"])
        self.assertIsNone(scored["distance_km"])
        self.assertNotIn("proximity", scored["score_basis"])

    def test_prefer_size_directions(self):
        small_lot, big_lot = self.row(), self.row(lot_size="Full Truckload")
        small_pref = ranking.score_row(small_lot, home=self.CHICAGO, prefer_size="small")
        big_pref = ranking.score_row(big_lot, home=self.CHICAGO, prefer_size="large")
        self.assertEqual(small_pref["score_size"], 1.0)
        self.assertEqual(big_pref["score_size"], 1.0)

    def test_prefer_size_any_removes_the_axis(self):
        scored = ranking.score_row(self.row(), home=self.CHICAGO, prefer_size="any")
        self.assertIsNone(scored["score_size"])
        self.assertNotIn("size", scored["score_basis"])

    def test_weights_change_the_outcome(self):
        near_big = self.row(lot_size="Full Truckload")
        far_small = self.row(location="Salem, OR")
        size_first = {"proximity": 0.0, "value": 0.0, "size": 1.0, "competition": 0.0}
        near_first = {"proximity": 1.0, "value": 0.0, "size": 0.0, "competition": 0.0}
        self.assertGreater(
            ranking.score_row(far_small, self.CHICAGO, weights=size_first)["score"],
            ranking.score_row(near_big, self.CHICAGO, weights=size_first)["score"],
        )
        self.assertGreater(
            ranking.score_row(near_big, self.CHICAGO, weights=near_first)["score"],
            ranking.score_row(far_small, self.CHICAGO, weights=near_first)["score"],
        )

    def test_score_is_the_renormalised_weighted_mean(self):
        scored = ranking.score_row(
            self.row(bid_to_msrp_pct=20.0, bids=None, lot_size=None),
            home=self.CHICAGO,
        )
        # Only proximity (1.0) and value (0.5) survive, weighted 0.30 each.
        self.assertEqual(scored["score_basis"], "proximity+value")
        self.assertEqual(scored["score"], 75.0)


class RankOrderTests(unittest.TestCase):
    CHICAGO = (41.88, -87.63)

    def setUp(self):
        self.rows = [
            {"auction_id": 1, "lot_size": "1 Pallet", "bid_to_msrp_pct": 1.0,
             "bids": 0, "location": "Salem, OR, US", "country": "US"},
            {"auction_id": 2, "lot_size": "1 Pallet", "bid_to_msrp_pct": 1.0,
             "bids": 0, "location": "Caledonia, MI, US", "country": "US"},
            {"auction_id": 3, "lot_size": "Full Truckload", "bid_to_msrp_pct": 1.0,
             "bids": 0, "location": "Caledonia, MI, US", "country": "US"},
        ]

    def test_nearby_small_lot_wins(self):
        ranked = ranking.rank_rows(self.rows, home=self.CHICAGO)
        self.assertEqual(ranked[0]["auction_id"], 2)

    def test_nearer_wins_among_equally_sized_lots(self):
        ranked = ranking.rank_rows(self.rows, home=self.CHICAGO)
        order = [row["auction_id"] for row in ranked]
        # 1 and 2 are both single pallets; 2 is far closer.
        self.assertLess(order.index(2), order.index(1))

    def test_smaller_wins_among_equidistant_lots(self):
        ranked = ranking.rank_rows(self.rows, home=self.CHICAGO)
        order = [row["auction_id"] for row in ranked]
        # 2 and 3 both ship from Caledonia; 2 is a single pallet.
        self.assertLess(order.index(2), order.index(3))

    def test_no_inversion_once_lots_exceed_a_long_haul(self):
        # Regression. With a linear ramp that bottomed out at 2500 km, both of
        # these scored proximity 0.0, distance stopped discriminating, and the
        # smaller-but-far Oregon lot beat the larger-but-near Tennessee one for
        # a Chicago buyer. The decay curve must keep Tennessee ahead.
        rows = [
            {"auction_id": "salem", "lot_size": "1 Pallet",
             "bid_to_msrp_pct": 1.03, "bids": 0,
             "location": "Salem, OR, US", "country": "US"},
            {"auction_id": "chattanooga", "lot_size": "2 Pallets",
             "bid_to_msrp_pct": 0.23, "bids": 0,
             "location": "Chattanooga, TN, US", "country": "US"},
        ]
        ranked = ranking.rank_rows(rows, home=self.CHICAGO)
        self.assertEqual(ranked[0]["auction_id"], "chattanooga")

    def test_ranks_are_contiguous_and_sorted(self):
        ranked = ranking.rank_rows(self.rows, home=self.CHICAGO)
        self.assertEqual([row["rank"] for row in ranked], [1, 2, 3])
        scores = [row["score"] for row in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_unscoreable_lots_sort_last(self):
        rows = self.rows + [{"auction_id": 9, "lot_size": None,
                             "bid_to_msrp_pct": None, "bids": None,
                             "location": None, "country": None}]
        ranked = ranking.rank_rows(rows, home=self.CHICAGO, prefer_size="any")
        self.assertEqual(ranked[-1]["auction_id"], 9)
        self.assertIsNone(ranked[-1]["score"])

    def test_rank_fields_match_the_declared_schema(self):
        ranked = ranking.rank_rows(self.rows, home=self.CHICAGO)
        for field in ranking.RANK_FIELDS:
            self.assertIn(field, ranked[0])

    def test_empty_input(self):
        self.assertEqual(ranking.rank_rows([], home=self.CHICAGO), [])


class ProximityCurveTests(unittest.TestCase):
    def test_zero_distance_is_a_perfect_score(self):
        self.assertEqual(ranking._proximity_score(0, 800), 1.0)

    def test_half_distance_scores_one_half(self):
        self.assertEqual(ranking._proximity_score(800, 800), 0.5)

    def test_strictly_decreasing_and_never_zero(self):
        distances = [0, 100, 400, 800, 1600, 2858, 7436, 20000]
        scores = [ranking._proximity_score(d, 800) for d in distances]
        for nearer, further in zip(scores, scores[1:]):
            self.assertGreater(nearer, further)
        self.assertGreater(scores[-1], 0.0)

    def test_scale_is_configurable(self):
        tight = ranking._proximity_score(800, 200)
        loose = ranking._proximity_score(800, 3000)
        self.assertLess(tight, loose)

    def test_missing_or_invalid_inputs(self):
        self.assertIsNone(ranking._proximity_score(None, 800))
        self.assertIsNone(ranking._proximity_score(100, 0))

    def test_caps_override_reaches_the_score(self):
        row = {"lot_size": "1 Pallet", "bid_to_msrp_pct": 0.0, "bids": 0,
               "location": "Chattanooga, TN, US", "country": "US"}
        tight = ranking.score_row(row, (41.88, -87.63),
                                  caps={"distance_half_km": 100.0})
        loose = ranking.score_row(row, (41.88, -87.63),
                                  caps={"distance_half_km": 5000.0})
        self.assertLess(tight["score_proximity"], loose["score_proximity"])


class WithinRadiusTests(unittest.TestCase):
    CHICAGO = (41.88, -87.63)

    def rows(self):
        return [
            {"auction_id": "near", "lot_size": "1 Pallet", "bid_to_msrp_pct": 1.0,
             "bids": 0, "location": "Caledonia, MI, US", "country": "US"},
            {"auction_id": "far", "lot_size": "1 Pallet", "bid_to_msrp_pct": 1.0,
             "bids": 0, "location": "Salem, OR, US", "country": "US"},
            {"auction_id": "unplaceable", "lot_size": "1 Pallet",
             "bid_to_msrp_pct": 1.0, "bids": 0, "location": None, "country": None},
        ]

    def test_drops_lots_beyond_the_radius(self):
        ranked = ranking.rank_rows(self.rows(), home=self.CHICAGO, within_km=600)
        self.assertEqual([row["auction_id"] for row in ranked], ["near"])

    def test_drops_lots_that_cannot_be_placed(self):
        # A radius cannot be honoured for a lot with no known location.
        ranked = ranking.rank_rows(self.rows(), home=self.CHICAGO, within_km=20000)
        self.assertNotIn("unplaceable", [row["auction_id"] for row in ranked])

    def test_ranks_are_renumbered_after_filtering(self):
        ranked = ranking.rank_rows(self.rows(), home=self.CHICAGO, within_km=5000)
        self.assertEqual([row["rank"] for row in ranked], [1, 2])

    def test_no_radius_keeps_everything(self):
        ranked = ranking.rank_rows(self.rows(), home=self.CHICAGO)
        self.assertEqual(len(ranked), 3)

    def test_radius_can_exclude_everything(self):
        self.assertEqual(
            ranking.rank_rows(self.rows(), home=self.CHICAGO, within_km=1), [])

    def test_boundary_is_inclusive(self):
        rows = self.rows()
        ranking.rank_rows(list(rows), home=self.CHICAGO)
        exact = next(r for r in rows if r["auction_id"] == "near")["distance_km"]
        kept = ranking.rank_rows(self.rows(), home=self.CHICAGO, within_km=exact)
        self.assertIn("near", [row["auction_id"] for row in kept])


class WeightParsingTests(unittest.TestCase):
    def test_defaults_are_returned_untouched(self):
        self.assertEqual(ranking.parse_weights([]), ranking.DEFAULT_WEIGHTS)

    def test_override_single_weight(self):
        self.assertEqual(ranking.parse_weights(["proximity=0.9"])["proximity"], 0.9)

    def test_rejects_bad_input(self):
        for bad in (["proximity"], ["speed=1"], ["proximity=soon"], ["proximity=-1"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ranking.parse_weights(bad)

    def test_rejects_all_zero_weights(self):
        with self.assertRaises(ValueError):
            ranking.parse_weights([
                "proximity=0", "value=0", "size=0", "competition=0"])

    def test_resolve_home_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            ranking.resolve_home("Atlantis")
        self.assertEqual(ranking.resolve_home("Chicago, IL")[1], "metro")


if __name__ == "__main__":
    unittest.main()
