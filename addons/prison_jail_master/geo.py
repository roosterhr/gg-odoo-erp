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

import difflib
import math
import re

EARTH_RADIUS_KM = 6371.0088

# Upper edge of each band, in whole km.
GREEN_MAX_KM = 70
YELLOW_MAX_KM = 150

# Two positions this close together are the same place (an open and a closed
# jail in one town, or the same town looked up twice).
SAME_PLACE_KM = 5

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


def agreed_point(points):
    """
    The one position a list of ``(lat, lng, approx)`` points stands for, or
    ``None`` when there are none or they lie in different places (nothing is
    guessed). Points within ``SAME_PLACE_KM`` of the first count as one place.
    """
    if not points:
        return None
    first = points[0]
    for point in points[1:]:
        if haversine_km(first[0], first[1], point[0], point[1]) > SAME_PLACE_KM:
            return None
    return first


def find_point(table, text):
    """Look a place name up in a ``{place_key: (lat, lng)}`` table."""
    key = place_key(text)
    return table.get(key) if key else None


# Words in a typed-in place that only say "district" or "taluk".
_DISTRICT_NOISE = frozenset({
    'district', 'distric', 'districtt', 'dstrict', 'dist', 'dsit', 'dt', 'tk', 'taluk', 'tq',
})
_DISTRICT_SUFFIXES = ('district', 'dist', 'dt')

# A typed-in name this similar to a known place (0 to 1) is taken to be a
# misspelling of it, unless a different place is almost as close.
FUZZY_CUTOFF = 0.82
FUZZY_MARGIN = 0.04
FUZZY_MIN_LENGTH = 6


def _clean_place(text):
    """Letters only, without district/taluk words, also when stuck to the name."""
    words = []
    for word in re.sub(r'[^a-z]+', ' ', (text or '').lower()).split():
        if word in _DISTRICT_NOISE:
            continue
        for suffix in _DISTRICT_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 5:
                word = word[:-len(suffix)]
                break
        words.append(word)
    return ' '.join(words)


def _fuzzy_point(table, key):
    """The one place whose key is clearly the closest to ``key``, else ``None``."""
    if len(key) < FUZZY_MIN_LENGTH:
        return None
    scored = sorted(
        ((difflib.SequenceMatcher(None, key, known).ratio(), table[known]) for known in table),
        key=lambda pair: pair[0], reverse=True,
    )
    best_ratio, best = scored[0]
    if best_ratio < FUZZY_CUTOFF:
        return None
    for ratio, point in scored[1:]:
        if point != best:
            return best if best_ratio - ratio >= FUZZY_MARGIN else None
    return best


def find_district_point(table, text):
    """
    Map point of a native place typed in by hand, from a ``{place_key: (lat, lng)}``
    table of districts and their spellings. ``None`` when there is no clear match.

    Tried in this order: the whole text, each part between commas, slashes or
    brackets, then each word, all as exact (spelling-tolerant) names; then the
    whole text and each part as a misspelling of a known place. Single words are
    never matched loosely, so "Madhura koothiramedu" is not taken for Madurai.
    """
    whole = _clean_place(text)
    if not whole:
        return None
    parts = [whole]
    for part in re.split(r'[,/;()]', text or ''):
        part = _clean_place(part)
        if part and part not in parts:
            parts.append(part)
    words = [w for w in whole.split() if w not in parts]

    for candidate in parts + words:
        point = find_point(table, candidate)
        if point:
            return point
    for candidate in parts:
        point = _fuzzy_point(table, place_key(candidate))
        if point:
            return point
    return None


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
