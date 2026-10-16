"""Coordinates for turning a lot's stated location into a distance.

B-Stock states where a lot ships from as a city and a US state ("Salem, OR")
or as a country ("Cabanillas del Campo, Madrid, ES"). To rank lots by how far
they have to travel we need a point for each.

Accuracy is deliberately coarse. A metro lookup resolves the warehouse towns
that actually recur in B-Stock titles; anything else falls back to a state or
country centroid, which can be a few hundred kilometres off. That is fine for
ranking lots against each other -- it is a freight proxy, not a routing engine.
"""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088

#: US metros and distribution towns, the precise tier of the lookup.
METRO_COORDS = {
    ("chicago", "IL"): (41.88, -87.63),
    ("joliet", "IL"): (41.53, -88.08),
    ("monee", "IL"): (41.42, -87.74),
    ("aurora", "IL"): (41.76, -88.32),
    ("edwardsville", "IL"): (38.81, -89.95),
    ("milwaukee", "WI"): (43.04, -87.91),
    ("kenosha", "WI"): (42.58, -87.82),
    ("indianapolis", "IN"): (39.77, -86.16),
    ("plainfield", "IN"): (39.70, -86.40),
    ("whitestown", "IN"): (39.99, -86.35),
    ("jeffersonville", "IN"): (38.28, -85.74),
    ("detroit", "MI"): (42.33, -83.05),
    ("romulus", "MI"): (42.22, -83.40),
    ("caledonia", "MI"): (42.79, -85.52),
    ("grand rapids", "MI"): (42.96, -85.67),
    ("columbus", "OH"): (39.96, -83.00),
    ("groveport", "OH"): (39.88, -82.88),
    ("etna", "OH"): (39.96, -82.68),
    ("cincinnati", "OH"): (39.10, -84.51),
    ("cleveland", "OH"): (41.50, -81.69),
    ("louisville", "KY"): (38.25, -85.76),
    ("minneapolis", "MN"): (44.98, -93.27),
    ("shakopee", "MN"): (44.80, -93.53),
    ("st. louis", "MO"): (38.63, -90.20),
    ("saint louis", "MO"): (38.63, -90.20),
    ("kansas city", "MO"): (39.10, -94.58),
    ("omaha", "NE"): (41.26, -95.93),
    ("des moines", "IA"): (41.59, -93.62),
    ("memphis", "TN"): (35.15, -90.05),
    ("nashville", "TN"): (36.16, -86.78),
    ("chattanooga", "TN"): (35.05, -85.31),
    ("lebanon", "TN"): (36.21, -86.29),
    ("atlanta", "GA"): (33.75, -84.39),
    ("union city", "GA"): (33.59, -84.54),
    ("savannah", "GA"): (32.08, -81.09),
    ("charlotte", "NC"): (35.23, -80.84),
    ("raleigh", "NC"): (35.78, -78.64),
    ("greensboro", "NC"): (36.07, -79.79),
    ("columbia", "SC"): (34.00, -81.03),
    ("spartanburg", "SC"): (34.95, -81.93),
    ("charleston", "SC"): (32.78, -79.93),
    ("birmingham", "AL"): (33.52, -86.80),
    ("bessemer", "AL"): (33.40, -86.95),
    ("jacksonville", "FL"): (30.33, -81.66),
    ("orlando", "FL"): (28.54, -81.38),
    ("tampa", "FL"): (27.95, -82.46),
    ("lakeland", "FL"): (28.04, -81.95),
    ("ruskin", "FL"): (27.72, -82.43),
    ("miami", "FL"): (25.76, -80.19),
    ("new orleans", "LA"): (29.95, -90.07),
    ("little rock", "AR"): (34.75, -92.29),
    ("dallas", "TX"): (32.78, -96.80),
    ("fort worth", "TX"): (32.76, -97.33),
    ("haslet", "TX"): (32.97, -97.35),
    ("houston", "TX"): (29.76, -95.37),
    ("san antonio", "TX"): (29.42, -98.49),
    ("austin", "TX"): (30.27, -97.74),
    ("el paso", "TX"): (31.76, -106.49),
    ("oklahoma city", "OK"): (35.47, -97.52),
    ("tulsa", "OK"): (36.15, -95.99),
    ("denver", "CO"): (39.74, -104.99),
    ("salt lake city", "UT"): (40.76, -111.89),
    ("phoenix", "AZ"): (33.45, -112.07),
    ("las vegas", "NV"): (36.17, -115.14),
    ("reno", "NV"): (39.53, -119.81),
    ("albuquerque", "NM"): (35.08, -106.65),
    ("boise", "ID"): (43.62, -116.20),
    ("los angeles", "CA"): (34.05, -118.24),
    ("san bernardino", "CA"): (34.11, -117.29),
    ("riverside", "CA"): (33.95, -117.40),
    ("stockton", "CA"): (37.96, -121.29),
    ("tracy", "CA"): (37.74, -121.43),
    ("sacramento", "CA"): (38.58, -121.49),
    ("san francisco", "CA"): (37.77, -122.42),
    ("portland", "OR"): (45.52, -122.68),
    ("salem", "OR"): (44.94, -123.04),
    ("seattle", "WA"): (47.61, -122.33),
    ("sumner", "WA"): (47.20, -122.24),
    ("baltimore", "MD"): (39.29, -76.61),
    ("washington", "DC"): (38.90, -77.04),
    ("philadelphia", "PA"): (39.95, -75.17),
    ("carlisle", "PA"): (40.20, -77.19),
    ("breinigsville", "PA"): (40.54, -75.63),
    ("hazleton", "PA"): (40.96, -75.98),
    ("pittsburgh", "PA"): (40.44, -80.00),
    ("newark", "NJ"): (40.74, -74.17),
    ("edison", "NJ"): (40.52, -74.41),
    ("robbinsville", "NJ"): (40.21, -74.62),
    ("new york", "NY"): (40.71, -74.01),
    ("rochester", "NY"): (43.16, -77.61),
    ("buffalo", "NY"): (42.89, -78.88),
    ("boston", "MA"): (42.36, -71.06),
    ("fall river", "MA"): (41.70, -71.15),
    ("hartford", "CT"): (41.76, -72.67),
    ("windsor", "CT"): (41.85, -72.64),
    ("richmond", "VA"): (37.54, -77.44),
    ("petersburg", "VA"): (37.23, -77.40),
    ("norfolk", "VA"): (36.85, -76.29),
}

#: Geographic centre of each US state, the fallback tier.
STATE_CENTROIDS = {
    "AL": (32.8, -86.8), "AK": (64.0, -152.0), "AZ": (34.2, -111.6),
    "AR": (34.9, -92.4), "CA": (37.2, -119.3), "CO": (39.0, -105.5),
    "CT": (41.6, -72.7), "DE": (39.0, -75.5), "DC": (38.9, -77.0),
    "FL": (28.6, -82.4), "GA": (32.6, -83.4), "HI": (20.3, -156.4),
    "ID": (44.4, -114.6), "IL": (40.0, -89.2), "IN": (39.9, -86.3),
    "IA": (42.1, -93.5), "KS": (38.5, -98.4), "KY": (37.5, -85.3),
    "LA": (31.0, -92.0), "ME": (45.4, -69.2), "MD": (39.0, -76.8),
    "MA": (42.3, -71.8), "MI": (44.3, -85.4), "MN": (46.3, -94.3),
    "MS": (32.7, -89.7), "MO": (38.4, -92.5), "MT": (47.0, -109.6),
    "NE": (41.5, -99.8), "NV": (39.3, -116.6), "NH": (43.7, -71.6),
    "NJ": (40.2, -74.7), "NM": (34.4, -106.1), "NY": (42.9, -75.5),
    "NC": (35.5, -79.4), "ND": (47.4, -100.5), "OH": (40.3, -82.8),
    "OK": (35.6, -97.5), "OR": (43.9, -120.6), "PA": (40.9, -77.8),
    "RI": (41.7, -71.6), "SC": (33.9, -80.9), "SD": (44.4, -100.2),
    "TN": (35.9, -86.4), "TX": (31.5, -99.3), "UT": (39.3, -111.7),
    "VT": (44.1, -72.7), "VA": (37.5, -78.9), "WA": (47.4, -120.4),
    "WV": (38.6, -80.6), "WI": (44.6, -89.7), "WY": (43.0, -107.6),
}

#: Country centroids, used for lots stated only by country (the EU marketplace).
COUNTRY_CENTROIDS = {
    "US": (39.8, -98.6), "CA": (56.1, -106.3), "MX": (23.6, -102.5),
    "GB": (54.0, -2.0), "IE": (53.2, -8.0), "ES": (40.2, -3.6),
    "PT": (39.6, -8.0), "IT": (42.8, 12.6), "FR": (46.6, 2.5),
    "DE": (51.1, 10.4), "NL": (52.2, 5.5), "BE": (50.6, 4.6),
    "PL": (52.1, 19.4), "SK": (48.7, 19.5), "CZ": (49.8, 15.5),
    "AT": (47.6, 14.1), "SE": (62.0, 15.0), "DK": (56.0, 10.0),
    "HU": (47.2, 19.5), "RO": (45.9, 25.0), "TR": (39.0, 35.2),
}


def haversine_km(origin, destination):
    """Great-circle distance in kilometres between two ``(lat, lon)`` points."""
    lat1, lon1 = radians(origin[0]), radians(origin[1])
    lat2, lon2 = radians(destination[0]), radians(destination[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    inner = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(inner))


def lookup_place(text):
    """Resolve free-form place text to ``(point, precision)``.

    Accepts ``"41.88,-87.63"``, ``"Chicago, IL"``, ``"IL"``, ``"US"`` and the
    forms B-Stock actually emits -- ``"Caledonia, MI, US"`` from US detail
    pages and ``"Cabanillas del Campo, Madrid, ES"`` from EU ones.

    ``precision`` is one of ``exact``, ``metro``, ``state`` or ``country`` and
    is reported on every ranked row, so a coarse placement is visible rather
    than silently trusted. Returns ``(None, None)`` if nothing resolves.
    """
    if not text:
        return None, None
    cleaned = " ".join(str(text).split())

    point = _as_coordinates(cleaned)
    if point:
        return point, "exact"

    tokens = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not tokens:
        return None, None
    upper = [token.upper() for token in tokens]

    # A US state sits either at the end ("Chicago, IL") or just before a
    # redundant country suffix ("Caledonia, MI, US").
    index = len(tokens) - 1
    if upper[index] in ("US", "USA") and len(tokens) >= 2:
        index -= 1

    state = upper[index]
    if state in STATE_CENTROIDS:
        if index >= 1:
            city = tokens[index - 1].lower()
            for candidate in (city, city.replace("st ", "st. "),
                              city.replace("st. ", "saint ")):
                if (candidate, state) in METRO_COORDS:
                    return METRO_COORDS[(candidate, state)], "metro"
        return STATE_CENTROIDS[state], "state"

    if upper[-1] in COUNTRY_CENTROIDS:
        return COUNTRY_CENTROIDS[upper[-1]], "country"
    return None, None


def _as_coordinates(text):
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return (lat, lon)
    return None


def locate_row(row):
    """Find the best available point for a scraped lot.

    Prefers the stated city/state, then the lot's country. The precision label
    travels with the point so a country-centroid placement is never mistaken
    for a real warehouse address.
    """
    point, precision = lookup_place(row.get("location"))
    if point:
        return point, precision

    country = (row.get("country") or "").strip().upper()
    if country in COUNTRY_CENTROIDS:
        return COUNTRY_CENTROIDS[country], "country"
    return None, None
