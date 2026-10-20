# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Starting coordinates for prisons that have none yet.

The seed file lists a town-level position for each institution. It is only a
starting point: a value already on the record is never overwritten, and every
seeded position is marked ``geo_source = 'approximate'`` so distances measured
to it are shown with a "~" until the department confirms the location.

Each row is matched to a ``prison.jail`` record in this order:

1. its XML ID, when that still points at an existing record;
2. its name, institution type and hierarchy, for databases where the jails
   were rebuilt and the original XML IDs no longer match;
3. the same name compared without case, spaces or punctuation.

A name that matches several records is skipped (never guessed), and a warning
is logged when many rows cannot be placed.
"""

import csv
import io
import logging
import re

from odoo.tools import file_open

_logger = logging.getLogger(__name__)

SEED_FILE = 'prison_jail_master/data/prison_jail_geo.csv'

# Log a warning when more than this share of the rows cannot be matched.
WARN_MISSING_SHARE = 0.10


def _name_key(name):
    """Name without case, spaces or punctuation, for the looser name match."""
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def _find_jail(Jail, row):
    """
    Return ``(jail, how)`` for a seed row. ``jail`` is empty when nothing
    matched and ``how`` says why: 'xml_id', 'name', 'name (loose)',
    'ambiguous' or 'missing'.
    """
    jail = Jail.env.ref(row['xml_id'], raise_if_not_found=False)
    if jail and jail._name == 'prison.jail' and jail.exists():
        return jail, 'xml_id'

    domain = [('name', '=', row['name'])]
    if row.get('jail_type'):
        domain.append(('jail_type', '=', row['jail_type']))
    if row.get('hierarchy_type'):
        domain.append(('hierarchy_type', '=', row['hierarchy_type']))
    found = Jail.search(domain)
    if len(found) == 1:
        return found, 'name'
    if len(found) > 1:
        return Jail.browse(), 'ambiguous'

    # Looser match, restricted to the same type and hierarchy.
    key = _name_key(row['name'])
    same_kind = [('jail_type', '=', row['jail_type'])] if row.get('jail_type') else []
    if row.get('hierarchy_type'):
        same_kind.append(('hierarchy_type', '=', row['hierarchy_type']))
    loose = Jail.search(same_kind).filtered(lambda j: _name_key(j.name) == key)
    if len(loose) == 1:
        return loose, 'name (loose)'
    return Jail.browse(), 'ambiguous' if len(loose) > 1 else 'missing'


def seed_prison_coordinates(env):
    """Fill blank prison coordinates from the seed file. Returns the count placed."""
    # Closed institutions are archived but still need a position.
    Jail = env['prison.jail'].with_context(active_test=False)
    placed = skipped = 0
    unmatched = []
    how_counts = {}
    with file_open(SEED_FILE, 'rb') as raw:
        rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8')))
    for row in rows:
        jail, how = _find_jail(Jail, row)
        if not jail:
            unmatched.append('%s (%s)' % (row['name'], how))
            continue
        how_counts[how] = how_counts.get(how, 0) + 1
        if jail.latitude or jail.longitude:
            skipped += 1
            continue
        jail.write({
            'latitude': float(row['latitude']),
            'longitude': float(row['longitude']),
            'geo_source': 'approximate',
        })
        placed += 1

    _logger.info(
        '[prison_jail_master] prison coordinates seeded: %s placed, %s already set, '
        '%s not matched (matched by %s)',
        placed, skipped, len(unmatched), how_counts or 'nothing',
    )
    # A shortfall is expected when a module that owns some of the institutions
    # (tnpd_prison_vacancy) is not installed yet, and is filled when it runs this.
    if unmatched and len(unmatched) > WARN_MISSING_SHARE * len(rows):
        _logger.warning(
            '[prison_jail_master] %s of %s seed institutions were not found or were '
            'ambiguous, so they have no coordinates and their officers will show '
            '"Distance unknown". First few: %s',
            len(unmatched), len(rows), '; '.join(unmatched[:8]),
        )
    elif unmatched:
        _logger.info('[prison_jail_master] not matched: %s', '; '.join(unmatched))
    return placed
