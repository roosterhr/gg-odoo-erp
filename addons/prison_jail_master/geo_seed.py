# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Starting coordinates for prisons that have none yet.

The seed file lists a town-level position for each institution. It is only a
starting point: a value already on the record is never overwritten, and every
seeded position is marked ``geo_source = 'approximate'`` so distances measured
to it are shown with a "~" until the department confirms the location.
"""

import csv
import io
import logging

from odoo.tools import file_open

_logger = logging.getLogger(__name__)

SEED_FILE = 'prison_jail_master/data/prison_jail_geo.csv'


def seed_prison_coordinates(env):
    """Fill blank prison coordinates from the seed file. Returns the count placed."""
    placed = skipped = missing = 0
    with file_open(SEED_FILE, 'rb') as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8')):
            jail = env.ref(row['xml_id'], raise_if_not_found=False)
            if not jail or jail._name != 'prison.jail':
                missing += 1
                continue
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
        '%s not found', placed, skipped, missing,
    )
    return placed
