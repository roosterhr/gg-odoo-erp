# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Geographical helpers for the officer distance label.

No Odoo imports here, so the rules can be unit-tested without a database
(see ``tests/test_geo.py``).

Distance is the straight-line (great-circle) km between the centre of an
officer's native district and the prison where they are posted. It is
rounded to whole km first, so the number shown on screen and the band it
falls in always agree.
"""

import math
import re

EARTH_RADIUS_KM = 6371.0088

# Upper edge of each band, in whole km.
GREEN_MAX_KM = 70
YELLOW_MAX_KM = 150

BAND_GREEN = 'green'
BAND_YELLOW = 'yellow'
BAND_RED = 'red'
BAND_UNKNOWN = 'unknown'

# (value, label) pairs for selection fields and filter dropdowns.
BAND_SELECTION = [
    (BAND_GREEN, '0 to 70 km'),
    (BAND_YELLOW, '71 to 150 km'),
    (BAND_RED, '>150 km'),
    (BAND_UNKNOWN, 'Unknown'),
]


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two latitude/longitude points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def whole_km(km):
    """Round half up, so 70.5 km becomes 71 (python's round() would give 70)."""
    return int(math.floor(km + 0.5))


def distance_band(km):
    """Band for a distance in km. ``None`` means the distance is not known."""
    if km is None:
        return BAND_UNKNOWN
    km = whole_km(km)
    if km <= GREEN_MAX_KM:
        return BAND_GREEN
    if km <= YELLOW_MAX_KM:
        return BAND_YELLOW
    return BAND_RED


_NOISE_WORDS = re.compile(r'\b(district|dist|dt|taluk)\b')


def place_key(text):
    """
    Spelling-tolerant key for a place name, so "Thiruvannamalai" and
    "Tiruvannamalai", or "Villupuram" and "Viluppuram", compare equal.
    """
    key = _NOISE_WORDS.sub(' ', (text or '').lower())
    key = re.sub(r'[^a-z]', '', key)
    for old, new in (('th', 't'), ('zh', 'l'), ('ee', 'i'), ('oo', 'u')):
        key = key.replace(old, new)
    return re.sub(r'(.)\1+', r'\1', key)


def find_point(table, text):
    """Look a place name up in a ``{place_key: (lat, lng)}`` table."""
    key = place_key(text)
    return table.get(key) if key else None


# Words that only say what kind of institution it is, not where it is. The
# institution type is a field of its own, so names are compared without them.
_KIND_WORDS = frozenset({
    'central', 'prison', 'prisons', 'jail', 'jails', 'district', 'dist',
    'sub', 'special', 'for', 'the', 'open', 'air', 'farm', 'transit', 'yard',
    'women', 'womens', 'woman', 'female', 'male', 'men', 'mens',
    'annex', 'closed', 'facility',
    # abbreviations: S.J., D.J., SPW, OAJ, WSJ
    's', 'j', 'd', 'sj', 'dj', 'spw', 'oaj', 'wsj',
})


def place_core(name):
    """
    The place part of an institution name, spelling-tolerant, so that
    "Central Prison, Coimbatore", "Coimbatore" and "S.J. Coimbatore (Closed)"
    give the same key. Compare it together with the institution type.
    """
    tokens = re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).split()
    return place_key(' '.join(t for t in tokens if t not in _KIND_WORDS))
