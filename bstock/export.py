"""Writers for CSV, JSON and JSON Lines output."""

import csv
import json
import sys

from .models import FIELDS

FORMATS = ("csv", "json", "jsonl", "table")


def _flatten(value):
    """Render list values for single-cell formats."""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return "" if value is None else value


def write(rows, fmt="csv", stream=None, fields=FIELDS):
    stream = stream or sys.stdout
    if fmt == "json":
        json.dump(rows, stream, indent=2, default=str)
        stream.write("\n")
    elif fmt == "jsonl":
        for row in rows:
            stream.write(json.dumps(row, default=str) + "\n")
    elif fmt == "csv":
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _flatten(row.get(key)) for key in fields})
    elif fmt == "table":
        # A caller that did not narrow the columns gets the readable subset
        # rather than all 30-odd fields smeared across the terminal.
        _write_table(rows, stream, None if tuple(fields) == FIELDS else fields)
    else:
        raise ValueError("unknown format {!r}; choose from {}".format(fmt, FORMATS))


#: Terminal view for a ranked run: why a lot won comes before what it is.
RANK_TABLE_FIELDS = (
    "rank",
    "score",
    "distance_km",
    "lot_size",
    "currency",
    "current_bid",
    "bid_to_msrp_pct",
    "units",
    "cost_per_unit",
    "bids",
    "location",
    "title",
)

#: A readable subset for terminal output; the full schema goes to CSV/JSON.
TABLE_FIELDS = (
    "auction_id",
    "currency",
    "current_bid",
    "msrp",
    "bid_to_msrp_pct",
    "units",
    "cost_per_unit",
    "bids",
    "country",
    "title",
)


def _write_table(rows, stream, fields=None):
    if not rows:
        stream.write("no lots matched\n")
        return
    columns = tuple(fields) if fields else TABLE_FIELDS
    widths = {}
    for field in columns:
        widths[field] = max(
            len(field), *(len(str(_flatten(row.get(field)))) for row in rows)
        )
    if "title" in widths:
        widths["title"] = min(widths["title"], 58)
    if "location" in widths:
        widths["location"] = min(widths["location"], 24)

    header = "  ".join(field.ljust(widths[field]) for field in columns)
    stream.write(header.rstrip() + "\n")
    stream.write("-" * min(len(header), 200) + "\n")
    for row in rows:
        cells = []
        for field in columns:
            text = str(_flatten(row.get(field)))
            if len(text) > widths[field]:
                text = text[: widths[field] - 1] + "…"
            cells.append(text.ljust(widths[field]))
        stream.write("  ".join(cells).rstrip() + "\n")
