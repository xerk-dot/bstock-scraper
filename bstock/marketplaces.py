"""The B-Stock Amazon marketplaces this scraper knows how to read."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Marketplace:
    """One B-Stock storefront.

    Attributes:
        key: Short name used on the command line.
        slug: Path segment under ``bstock.com`` (e.g. ``amazon``).
        site_abb: Marketplace abbreviation the image service expects.
        label: Human-readable marketplace name.
        currencies: Currencies lots are denominated in, for documentation only.
    """

    key: str
    slug: str
    site_abb: str
    label: str
    currencies: tuple

    @property
    def base_url(self):
        return "https://bstock.com/{}".format(self.slug)

    @property
    def list_url(self):
        return "{}/auction/auction/list/".format(self.base_url)

    def auction_url(self, auction_id):
        return "{}/auction/auction/view/id/{}/".format(self.base_url, auction_id)


US = Marketplace(
    key="us",
    slug="amazon",
    site_abb="amz",
    label="Amazon Liquidation Auctions (US)",
    currencies=("USD",),
)

EU = Marketplace(
    key="eu",
    slug="amazoneu",
    site_abb="a2z",
    label="Amazon Europe Online Liquidation Auctions",
    currencies=("EUR", "GBP"),
)

MARKETPLACES = {m.key: m for m in (US, EU)}

#: Values accepted by the listing page's ``order`` parameter.
SORT_FIELDS = (
    "end_time",
    "start_time",
    "current_bid_amt",
    "number_bids",
    "name",
)

#: Sidebar facets the listing page filters on. Values are marketplace-specific
#: numeric ids (or ISO codes for country/currency), so they are discovered at
#: runtime rather than hardcoded -- see :mod:`bstock.facets`.
FILTER_PARAMS = (
    "condition",
    "inventory_type",
    "product_type",
    "shipment_type",
    "country",
    "currency",
)


def get(key):
    """Look up a marketplace by key, raising a helpful error if unknown."""
    try:
        return MARKETPLACES[key]
    except KeyError:
        raise KeyError(
            "unknown marketplace {!r}; choose from {}".format(
                key, ", ".join(sorted(MARKETPLACES))
            )
        )
