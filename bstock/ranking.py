"""Rank lots for a particular buyer: how near, how cheap, how big, how contested.

Scoring is intentionally transparent. Each axis is scored 0-1 against an
absolute, documented threshold rather than against the rest of the batch, so a
lot's score means the same thing from one run to the next. Every component is
written back onto the row, so a ranking can always be taken apart and checked.
"""

import re

from .geo import haversine_km, locate_row, lookup_place

#: How much freight a lot represents, in pallet-equivalents.
_SIZE_UNITS = (
    (re.compile(r"(\d+)\s*pallet", re.I), lambda m: float(m.group(1))),
    (re.compile(r"full\s+truckload", re.I), lambda m: 26.0),
    (re.compile(r"small\s+truckload", re.I), lambda m: 12.0),
    (re.compile(r"truckload", re.I), lambda m: 24.0),
    (re.compile(r"\bltl\b", re.I), lambda m: 4.0),
    (re.compile(r"(\d+)\s*box", re.I), lambda m: round(0.4 * float(m.group(1)), 2)),
    (re.compile(r"(\d+)\s*case", re.I), lambda m: round(0.2 * float(m.group(1)), 2)),
)

DEFAULT_WEIGHTS = {
    "proximity": 0.30,
    "value": 0.30,
    "size": 0.25,
    "competition": 0.15,
}

DEFAULT_CAPS = {
    # Distance at which the proximity score bottoms out.
    "max_distance_km": 2500.0,
    # bid-to-MSRP percentage at which the value score bottoms out.
    "msrp_pct": 40.0,
    # Pallet-equivalents at which the size score bottoms out.
    "size_units": 8.0,
    # Bid count at which the competition score bottoms out.
    "bids": 15.0,
}

RANK_FIELDS = (
    "rank",
    "score",
    "distance_km",
    "geo_precision",
    "size_units",
    "score_proximity",
    "score_value",
    "score_size",
    "score_competition",
    "score_basis",
)


def size_units(lot_size):
    """Pallet-equivalents for a lot-size phrase, or ``None`` if unrecognised."""
    if not lot_size:
        return None
    for pattern, convert in _SIZE_UNITS:
        match = pattern.search(lot_size)
        if match:
            return convert(match)
    return None


def _ramp(value, cap):
    """Map ``value`` onto 0-1 against ``cap``, where smaller is better.

    Zero scores 1.0; anything at or beyond ``cap`` scores 0.0.
    """
    if value is None or not cap or cap <= 0:
        return None
    fraction = min(max(float(value) / float(cap), 0.0), 1.0)
    return round(1.0 - fraction, 4)


def _size_score(units, cap, prefer):
    """Score lot size, measuring from one pallet rather than from zero.

    A single pallet is the smallest lot B-Stock actually sells, so it is the
    ideal for a small-lot buyer and must score 1.0. Ramping from zero would
    dock it for not being an imaginary empty lot.
    """
    if units is None or prefer == "any":
        return None
    if cap is None or cap <= 1:
        return None
    fraction = min(max((float(units) - 1.0) / (float(cap) - 1.0), 0.0), 1.0)
    return round(fraction if prefer == "large" else 1.0 - fraction, 4)


def resolve_home(text):
    """Resolve the buyer's location, raising ValueError if it cannot be placed."""
    point, precision = lookup_place(text)
    if point is None:
        raise ValueError(
            "could not place home location {!r}; use a US metro "
            '("Chicago, IL"), a state code ("IL"), a country code, or '
            '"lat,lon"'.format(text)
        )
    return point, precision


def score_row(row, home=None, prefer_size="small", weights=None, caps=None):
    """Score one lot, returning the rank fields to merge onto it."""
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    caps = dict(DEFAULT_CAPS, **(caps or {}))

    distance = None
    precision = None
    if home is not None:
        point, precision = locate_row(row)
        if point is not None:
            distance = round(haversine_km(home, point), 1)

    units = size_units(row.get("lot_size"))

    size_score = _size_score(units, caps["size_units"], prefer_size)

    parts = {
        "proximity": _ramp(distance, caps["max_distance_km"]),
        "value": _ramp(row.get("bid_to_msrp_pct"), caps["msrp_pct"]),
        "size": size_score,
        "competition": _ramp(row.get("bids"), caps["bids"]),
    }

    # Axes without data are dropped and the remaining weights renormalised, so
    # a missing field never silently counts as a zero.
    used = {name: value for name, value in parts.items() if value is not None}
    total_weight = sum(weights.get(name, 0.0) for name in used)
    if total_weight > 0:
        score = sum(weights.get(name, 0.0) * value for name, value in used.items())
        score = round(100.0 * score / total_weight, 2)
    else:
        score = None

    return {
        "rank": None,
        "score": score,
        "distance_km": distance,
        "geo_precision": precision,
        "size_units": units,
        "score_proximity": parts["proximity"],
        "score_value": parts["value"],
        "score_size": parts["size"],
        "score_competition": parts["competition"],
        "score_basis": "+".join(sorted(used)) or None,
    }


def rank_rows(rows, home=None, prefer_size="small", weights=None, caps=None):
    """Score and sort lots best-first, numbering them from 1.

    Unscoreable lots sort last. Returns a new list; input rows are updated in
    place with their rank fields.
    """
    for row in rows:
        row.update(score_row(row, home, prefer_size, weights, caps))

    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("score") is None,
            -(row.get("score") or 0.0),
            row.get("distance_km") if row.get("distance_km") is not None else 1e9,
        ),
    )
    for position, row in enumerate(ordered, start=1):
        row["rank"] = position
    return ordered


def parse_weights(pairs, parser_error=None):
    """Parse ``name=value`` weight overrides into a weights dict."""
    weights = dict(DEFAULT_WEIGHTS)
    for item in pairs or []:
        if "=" not in item:
            message = "--weight expects NAME=VALUE, got {!r}".format(item)
            if parser_error:
                parser_error(message)
            raise ValueError(message)
        name, raw = item.split("=", 1)
        name = name.strip()
        if name not in DEFAULT_WEIGHTS:
            message = "unknown weight {!r}; choose from {}".format(
                name, ", ".join(sorted(DEFAULT_WEIGHTS))
            )
            if parser_error:
                parser_error(message)
            raise ValueError(message)
        try:
            value = float(raw)
        except ValueError:
            message = "weight {} must be a number, got {!r}".format(name, raw)
            if parser_error:
                parser_error(message)
            raise ValueError(message)
        if value < 0:
            message = "weight {} must not be negative".format(name)
            if parser_error:
                parser_error(message)
            raise ValueError(message)
        weights[name] = value
    if sum(weights.values()) <= 0:
        message = "at least one weight must be greater than zero"
        if parser_error:
            parser_error(message)
        raise ValueError(message)
    return weights
