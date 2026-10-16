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
        _write_table(rows, stream)
    else:
        raise ValueError("unknown format {!r}; choose from {}".format(fmt, FORMATS))


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


def _write_table(rows, stream):
    if not rows:
        stream.write("no lots matched\n")
        return
    widths = {}
    for field in TABLE_FIELDS:
        widths[field] = max(
            len(field), *(len(str(_flatten(row.get(field)))) for row in rows)
        )
    widths["title"] = min(widths.get("title", 10), 58)

    header = "  ".join(field.ljust(widths[field]) for field in TABLE_FIELDS)
    stream.write(header.rstrip() + "\n")
    stream.write("-" * min(len(header), 200) + "\n")
    for row in rows:
        cells = []
        for field in TABLE_FIELDS:
            text = str(_flatten(row.get(field)))
            if len(text) > widths[field]:
                text = text[: widths[field] - 1] + "…"
            cells.append(text.ljust(widths[field]))
        stream.write("  ".join(cells).rstrip() + "\n")
