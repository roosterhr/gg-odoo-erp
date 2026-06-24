# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Fixes hierarchy_type on SPW records that were incorrectly left at 'general'
# because the 19.0.2.1.0 migration only updated rows with NULL/empty values,
# but the ORM adds columns with DEFAULT 'general', so SPW rows were never updated.

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Run automatically by Odoo when prison_jail_master is upgraded to 19.0.2.2.0.
    Safe to re-run — idempotent.
    """
    _logger.info('[prison_jail_master] post-migrate 19.0.2.2.0 — start')

    _fix_spw_hierarchy_type(cr)

    _logger.info('[prison_jail_master] post-migrate 19.0.2.2.0 — done')


def _fix_spw_hierarchy_type(cr):
    """
    Unconditionally set hierarchy_type = 'women' for all SPW records.

    The 19.0.2.1.0 migration used (IS NULL OR = '') as the guard, which missed
    rows that already had the ORM default 'general' applied.  This migration
    corrects them regardless of current value.
    """
    cr.execute("""
        UPDATE prison_jail
           SET hierarchy_type = 'women'
         WHERE jail_type = 'spw'
           AND hierarchy_type != 'women';
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] fixed hierarchy_type on %d SPW records', cr.rowcount)
    else:
        _logger.info('[prison_jail_master] SPW hierarchy_type already correct — nothing to update')
