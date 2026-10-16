"""Command-line entry point."""

import argparse
import sys

from . import __version__, export, http, marketplaces, ranking, scraper
from .models import FIELDS

EPILOG = """\
examples:
  # what's live in the EU marketplace, newest closing first
  bstock --marketplace eu --max-items 20 --format table

  # see which filters the marketplace currently offers, with live counts
  bstock --marketplace eu --list-filters

  # Polish customer-return lots to CSV, listing data only (fast)
  bstock --marketplace eu --filter country=PL --no-details --out lots.csv

  # both marketplaces, full enrichment, newline-delimited JSON
  bstock --marketplace us,eu --max-items 100 --format jsonl --out lots.jsonl

  # rank US lots for a Chicago buyer who wants small pallets
  bstock -m us --rank --home "Chicago, IL" --format table

  # same, but weight freight distance above everything else
  bstock -m us --rank --home "Chicago, IL" --weight proximity=0.6 --weight value=0.2

  # hard radius: only lots within 600 km of home
  bstock -m us --rank --home "Chicago, IL" --within 600
"""


class StderrProgress(scraper.Progress):
    """Prints progress to stderr so stdout stays pipeable."""

    def __init__(self, quiet=False):
        self.quiet = quiet

    def _say(self, message):
        if not self.quiet:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()

    def page(self, marketplace, number, found, total):
        suffix = " of {} reported".format(total) if total is not None else ""
        self._say("  {}: page {} -> {} new lot(s){}".format(
            marketplace, number, found, suffix))

    def stage(self, message):
        self._say("  " + message)

    def warn(self, message):
        self._say("  ! " + message)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="bstock",
        description=(
            "Scrape public B-Stock Amazon liquidation auction listings. "
            "Read-only: never logs in, never bids."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="bstock " + __version__)

    parser.add_argument(
        "-m", "--marketplace", default="us,eu",
        help="comma-separated marketplaces: {} (default: us,eu)".format(
            ", ".join(sorted(marketplaces.MARKETPLACES))),
    )
    parser.add_argument(
        "-n", "--max-items", type=int, default=None,
        help="stop after this many lots per marketplace (default: all)",
    )
    parser.add_argument(
        "--filter", action="append", default=[], metavar="KEY=VALUE",
        help=(
            "listing filter, repeatable, e.g. --filter country=PL. Valid keys: "
            + ", ".join(marketplaces.FILTER_PARAMS)
            + ". Run --list-filters for the values this marketplace accepts."
        ),
    )
    parser.add_argument(
        "--list-filters", action="store_true",
        help="print the filters and live facet counts, then exit",
    )
    parser.add_argument(
        "--sort", choices=marketplaces.SORT_FIELDS, default=None,
        help="sort field (default: the site's own ordering)",
    )
    parser.add_argument(
        "--dir", dest="direction", choices=("asc", "desc"), default=None,
        help="sort direction",
    )
    parser.add_argument(
        "--page-size", type=int, choices=scraper.PAGE_SIZES, default=48,
        help="lots per listing request (default: 48)",
    )

    group = parser.add_argument_group("enrichment")
    group.add_argument(
        "--no-details", action="store_true",
        help="skip detail pages (much faster; drops condition, location, fees)",
    )
    group.add_argument(
        "--no-images", action="store_true",
        help="skip the lot asset lookup (drops image URLs and ASINs)",
    )
    group.add_argument(
        "--image-size", default="thumb",
        help="image rendition to record, e.g. thumb or original (default: thumb)",
    )
    group.add_argument(
        "--max-images", type=int, default=10,
        help="cap images recorded per lot (default: 10)",
    )

    group = parser.add_argument_group("ranking")
    group.add_argument(
        "--rank", action="store_true",
        help="score and sort lots best-first; adds the score columns",
    )
    group.add_argument(
        "--home", default=None, metavar="PLACE",
        help=(
            'where the lot has to ship to, e.g. "Chicago, IL", "IL", "GB" or '
            '"41.88,-87.63". Without it the proximity axis is skipped.'
        ),
    )
    group.add_argument(
        "--prefer-size", choices=("small", "large", "any"), default="small",
        help="lot-size preference (default: small)",
    )
    group.add_argument(
        "--weight", action="append", default=[], metavar="NAME=VALUE",
        help=(
            "override a scoring weight, repeatable. Defaults: "
            + ", ".join("{}={}".format(name, value)
                        for name, value in sorted(ranking.DEFAULT_WEIGHTS.items()))
        ),
    )
    group.add_argument(
        "--within", type=float, default=None, metavar="KM",
        help=(
            "hard radius: drop lots further than this from --home, and lots "
            "whose location cannot be resolved. Unlike the scoring weights, "
            "this removes rows rather than just ranking them lower."
        ),
    )
    group.add_argument(
        "--distance-half", type=float,
        default=ranking.DEFAULT_CAPS["distance_half_km"], metavar="KM",
        help=(
            "distance at which the proximity score falls to 0.5; the curve "
            "decays smoothly from there (default: %(default)s)"
        ),
    )
    group.add_argument(
        "--msrp-cap", type=float, default=ranking.DEFAULT_CAPS["msrp_pct"],
        metavar="PCT",
        help="bid-to-MSRP %% at which the value score reaches zero (default: %(default)s)",
    )
    group.add_argument(
        "--size-cap", type=float, default=ranking.DEFAULT_CAPS["size_units"],
        metavar="PALLETS",
        help="pallet-equivalents at which the size score reaches zero (default: %(default)s)",
    )
    group.add_argument(
        "--bids-cap", type=float, default=ranking.DEFAULT_CAPS["bids"],
        metavar="N",
        help="bid count at which the competition score reaches zero (default: %(default)s)",
    )
    group.add_argument(
        "--top", type=int, default=None, metavar="N",
        help="keep only the best N lots after ranking",
    )

    group = parser.add_argument_group("output")
    group.add_argument(
        "-f", "--format", choices=export.FORMATS, default="csv",
        help="output format (default: csv)",
    )
    group.add_argument(
        "-o", "--out", default=None, help="write to this file instead of stdout",
    )
    group.add_argument(
        "--fields", default=None,
        help="comma-separated subset of columns for csv/table output",
    )
    group.add_argument("-q", "--quiet", action="store_true", help="suppress progress")

    group = parser.add_argument_group("politeness")
    group.add_argument(
        "--delay", type=float, default=1.0,
        help="minimum seconds between requests (default: 1.0)",
    )
    group.add_argument("--timeout", type=float, default=30.0, help="per-request timeout")
    group.add_argument("--retries", type=int, default=3, help="retries per request")
    group.add_argument("--user-agent", default=http.DEFAULT_USER_AGENT)
    group.add_argument(
        "--ignore-robots", action="store_true",
        help="do not consult robots.txt (you are responsible for this choice)",
    )
    return parser


def parse_filters(raw_filters, parser):
    filters = {}
    for item in raw_filters:
        if "=" not in item:
            parser.error("--filter expects KEY=VALUE, got {!r}".format(item))
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in marketplaces.FILTER_PARAMS:
            parser.error(
                "unknown filter {!r}; valid keys: {}".format(
                    key, ", ".join(marketplaces.FILTER_PARAMS)
                )
            )
        filters[key] = value
    return filters


def resolve_marketplaces(raw, parser):
    chosen = []
    for key in (part.strip() for part in raw.split(",")):
        if not key:
            continue
        try:
            chosen.append(marketplaces.get(key))
        except KeyError as exc:
            parser.error(str(exc))
    if not chosen:
        parser.error("no marketplace selected")
    return chosen


def print_filters(session, selected, stream):
    for marketplace in selected:
        stream.write("\n{} [{}]\n".format(marketplace.label, marketplace.key))
        facets = scraper.discover_facets(session, marketplace)
        if not facets:
            stream.write("  (no filters offered right now)\n")
            continue
        for param in marketplaces.FILTER_PARAMS:
            if param not in facets:
                continue
            stream.write("  {}\n".format(param))
            for option in facets[param]:
                count = "" if option["count"] is None else " ({} lots)".format(
                    option["count"])
                stream.write("    --filter {}={:<10} {}{}\n".format(
                    param, option["value"], option["label"], count))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    selected = resolve_marketplaces(args.marketplace, parser)
    filters = parse_filters(args.filter, parser)

    if args.top is not None and not args.rank:
        parser.error("--top only applies with --rank")
    if args.within is not None and not args.rank:
        parser.error("--within only applies with --rank")
    if args.within is not None and not args.home:
        parser.error("--within needs --home to measure a radius from")

    weights = ranking.parse_weights(args.weight, parser.error)
    caps = {
        "distance_half_km": args.distance_half,
        "msrp_pct": args.msrp_cap,
        "size_units": args.size_cap,
        "bids": args.bids_cap,
    }

    home = None
    if args.home:
        try:
            home, home_precision = ranking.resolve_home(args.home)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.rank:
        sys.stderr.write(
            "note: --rank without --home skips the proximity axis; "
            'pass e.g. --home "Chicago, IL"\n'
        )

    available = FIELDS + (ranking.RANK_FIELDS if args.rank else ())
    fields = available
    if args.fields:
        fields = tuple(f.strip() for f in args.fields.split(",") if f.strip())
        unknown = [f for f in fields if f not in available]
        if unknown:
            parser.error("unknown field(s): {}".format(", ".join(unknown)))

    session = http.Session(
        user_agent=args.user_agent,
        delay=args.delay,
        timeout=args.timeout,
        retries=args.retries,
        respect_robots=not args.ignore_robots,
    )
    progress = StderrProgress(quiet=args.quiet)

    if args.list_filters:
        print_filters(session, selected, sys.stdout)
        return 0

    rows = []
    try:
        for marketplace in selected:
            progress.stage("{} ({})".format(marketplace.label, marketplace.key))
            market_rows, meta = scraper.scrape_marketplace(
                session,
                marketplace,
                filters=filters,
                sort=args.sort,
                direction=args.direction,
                page_size=args.page_size,
                max_items=args.max_items,
                fetch_details=not args.no_details,
                fetch_images=not args.no_images,
                image_size=args.image_size,
                max_images=args.max_images,
                progress=progress,
            )
            rows.extend(market_rows)
            progress.stage("{}: {} lot(s) collected".format(
                marketplace.key, meta["returned"]))
    except http.BlockedError as exc:
        sys.stderr.write("\nblocked: {}\n".format(exc))
        return 3
    except http.RobotsDenied as exc:
        sys.stderr.write("\nrobots.txt: {}\n".format(exc))
        return 4
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted; writing what was collected\n")
    except http.ScrapeError as exc:
        sys.stderr.write("\nerror: {}\n".format(exc))
        return 1

    if args.rank:
        collected = len(rows)
        rows = ranking.rank_rows(
            rows, home=home, prefer_size=args.prefer_size,
            weights=weights, caps=caps, within_km=args.within,
        )
        if args.within is not None:
            dropped = collected - len(rows)
            if dropped:
                progress.warn(
                    "--within {:g} km dropped {} of {} lot(s) as too far away "
                    "or unplaceable".format(args.within, dropped, collected)
                )
        elif home is not None:
            unplaced = sum(1 for row in rows if row.get("distance_km") is None)
            if unplaced:
                progress.warn(
                    "{} lot(s) could not be placed on a map; ranked without "
                    "the proximity axis".format(unplaced)
                )
        if args.top:
            rows = rows[: args.top]
        if args.format == "table" and not args.fields:
            fields = export.RANK_TABLE_FIELDS

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            export.write(rows, fmt=args.format, stream=handle, fields=fields)
        progress.stage("wrote {} row(s) to {}".format(len(rows), args.out))
    else:
        export.write(rows, fmt=args.format, stream=sys.stdout, fields=fields)

    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
