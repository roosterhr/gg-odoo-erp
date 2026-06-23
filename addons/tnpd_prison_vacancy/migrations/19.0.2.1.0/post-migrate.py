# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Recomputes prison.vacancy aggregate totals from prison.designation.vacancy
# so the hierarchy page matches the vacancy dashboard immediately after deploy.

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Run automatically by Odoo when tnpd_prison_vacancy is upgraded to 19.0.2.1.0.
    Recomputes prison.vacancy totals from designation-level records.
    Safe to re-run — fully idempotent.
    """
    if not version:
        # Fresh install — seed XML loads correct data already.
        return

    _logger.info('[tnpd_prison_vacancy] post-migrate 19.0.2.1.0 — recompute vacancy totals')

    cr.execute("""
        UPDATE prison_vacancy pv
           SET sanctioned_strength = agg.sanctioned,
               occupied_count      = agg.filled,
               vacancy_count       = agg.vacancy
          FROM (
              SELECT prison_id,
                     SUM(sanctioned_strength) AS sanctioned,
                     SUM(filled_strength)     AS filled,
                     SUM(vacancy_count)       AS vacancy
                FROM prison_designation_vacancy
               GROUP BY prison_id
          ) agg
         WHERE pv.prison_id = agg.prison_id;
    """)
    _logger.info(
        '[tnpd_prison_vacancy] recomputed %d prison.vacancy records from designation data',
        cr.rowcount,
    )
