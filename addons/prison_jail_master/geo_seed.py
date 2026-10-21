# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Starting coordinates for prisons that have none yet.

The seed file lists a town-level position for each institution. It is only a
starting point: a value already on the record is never overwritten, and every
seeded position is marked ``geo_source = 'approximate'`` so distances measured
to it are shown with a "~" until the department confirms the location.

Databases do not always keep the names and XML IDs of the seed data (jails get
rebuilt, central prisons are named just "Coimbatore" instead of "Central
Prison, Coimbatore"), so a position is found in three passes:

1. Per seed row: its XML ID, then its name with the same institution type and
   hierarchy, then the same name compared without case, spaces or punctuation.
2. Per institution still without a position: the place part of its name
   (``geo.place_core``, which ignores words such as "Central Prison", "S.J." or
   "District") with the same institution type and hierarchy. Used only when the
   seed has one position for that place; if it has several, nothing is guessed.
3. The only institution of its kind on both sides.

Institutions that still have no position are logged by name.
"""

import collections
import csv
import io
import logging
import re

from odoo.tools import file_open

from . import geo

_logger = logging.getLogger(__name__)

SEED_FILE = 'prison_jail_master/data/prison_jail_geo.csv'

# Log a warning when more than this share of the institutions has no position.
WARN_MISSING_SHARE = 0.10

# Seed rows for one place whose positions lie within this distance of each
# other are the same place (an open and a closed jail in one town).
SAME_PLACE_KM = 5


def _name_key(name):
    """Name without case, spaces or punctuation, for the looser name match."""
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def _has_position(jail):
    return bool(jail.latitude or jail.longitude)


def _find_jail(Jail, row):
    """Pass 1: the institution a seed row refers to, or an empty recordset."""
    jail = Jail.env.ref(row['xml_id'], raise_if_not_found=False)
    if jail and jail._name == 'prison.jail' and jail.exists():
        return jail, 'xml_id'

    same_kind = []
    if row.get('jail_type'):
        same_kind.append(('jail_type', '=', row['jail_type']))
    if row.get('hierarchy_type'):
        same_kind.append(('hierarchy_type', '=', row['hierarchy_type']))

    found = Jail.search([('name', '=', row['name'])] + same_kind)
    if len(found) == 1:
        return found, 'name'
    if found:
        return Jail.browse(), 'ambiguous'

    key = _name_key(row['name'])
    loose = Jail.search(same_kind).filtered(lambda j: _name_key(j.name) == key)
    if len(loose) == 1:
        return loose, 'name (loose)'
    return Jail.browse(), 'ambiguous' if loose else 'missing'


def _same_place(rows):
    """True when all the rows sit within SAME_PLACE_KM of the first one."""
    first = rows[0]
    lat, lng = float(first['latitude']), float(first['longitude'])
    return all(
        geo.haversine_km(lat, lng, float(r['latitude']), float(r['longitude'])) <= SAME_PLACE_KM
        for r in rows[1:]
    )


def _position(row):
    return {
        'latitude': float(row['latitude']),
        'longitude': float(row['longitude']),
        'geo_source': 'approximate',
    }


def seed_from_rows(env, rows):
    """Seed blank positions from ``rows`` (dicts shaped like the seed file)."""
    # Closed institutions are archived but still need a position.
    Jail = env['prison.jail'].with_context(active_test=False)
    how = collections.Counter()
    counts = {'placed': 0, 'skipped': 0}

    def place(jail, row, why):
        how[why] += 1
        if _has_position(jail):
            counts['skipped'] += 1
            return
        jail.write(_position(row))
        counts['placed'] += 1

    # Pass 1: from each seed row to its institution.
    for row in rows:
        jail, why = _find_jail(Jail, row)
        if jail:
            place(jail, row, why)

    # Pass 2: from each institution still without a position to a seed row, by
    # the place part of its name. When several rows share that place name they
    # must all be the same place (see SAME_PLACE_KM); otherwise nothing is guessed.
    by_place = collections.defaultdict(list)
    by_kind = collections.defaultdict(list)
    for row in rows:
        kind = (row.get('jail_type'), row.get('hierarchy_type'))
        by_place[kind + (geo.place_core(row['name']),)].append(row)
        by_kind[kind].append(row)

    blank = Jail.search([]).filtered(lambda j: not _has_position(j))
    for jail in blank:
        core = geo.place_core(jail.name)
        candidates = by_place.get((jail.jail_type, jail.hierarchy_type, core)) if core else None
        if candidates and _same_place(candidates):
            place(jail, candidates[0], 'place name')

    # Pass 3: the only institution of its kind on both sides.
    blank = [j for j in blank if not _has_position(j)]
    for kind, kind_rows in by_kind.items():
        mine = [j for j in blank if (j.jail_type, j.hierarchy_type) == kind]
        if len(kind_rows) == 1 and len(mine) == 1:
            place(mine[0], kind_rows[0], 'only one of its kind')

    still = Jail.search([]).filtered(lambda j: not _has_position(j))
    total = Jail.search_count([])
    _logger.info(
        '[prison_jail_master] prison coordinates seeded: %s placed, %s already set, '
        '%s of %s institutions still without a position (matched by %s)',
        counts['placed'], counts['skipped'], len(still), total, dict(how) or 'nothing',
    )
    if still and len(still) > WARN_MISSING_SHARE * total:
        _logger.warning(
            '[prison_jail_master] %s of %s institutions have no position, so their '
            'officers will show "Distance unknown". Names (type): %s',
            len(still), total,
            '; '.join('%s (%s)' % (j.name, j.jail_type) for j in still[:15]),
        )
    elif still:
        _logger.info(
            '[prison_jail_master] without a position: %s',
            '; '.join('%s (%s)' % (j.name, j.jail_type) for j in still),
        )
    return counts['placed']


def seed_prison_coordinates(env):
    """Fill blank prison coordinates from the seed file. Returns the count placed."""
    with file_open(SEED_FILE, 'rb') as raw:
        rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8')))
    return seed_from_rows(env, rows)
