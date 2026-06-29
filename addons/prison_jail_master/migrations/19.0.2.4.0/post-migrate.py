# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Fixes duplicate closed sub-jails created by 19.0.2.3.0 migration.
# Dev had extra sub_jail records with the same name under different parents;
# the previous migration marked all name-matches, not just the correct ones.

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info('[prison_jail_master] post-migrate 19.0.2.4.0 — start')

    _deactivate_wrong_duplicates(cr)
    _fix_cuddalore_women_subjail(cr)

    _logger.info('[prison_jail_master] post-migrate 19.0.2.4.0 — done')


def _deactivate_wrong_duplicates(cr):
    """
    Keeranur and Pattukottai each have two sub_jail records in dev.
    Only the ones matching local (canonical) parents are correct per GO-2025.
    Deactivate the wrong duplicates identified by their parent district jail name.
    """
    # Keeranur under Pudukkottai is wrong — correct one is under Tiruchirappalli
    cr.execute("""
        UPDATE prison_jail pj
           SET active = FALSE,
               is_closed = FALSE
          FROM prison_jail parent
         WHERE pj.name = 'Keeranur'
           AND pj.jail_type = 'sub_jail'
           AND pj.parent_id = parent.id
           AND parent.name ILIKE '%Pudukkottai%';
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] deactivated wrong Keeranur (Pudukkottai) duplicate')

    # Pattukottai under Chennai is wrong — correct one is under Thanjavur District
    cr.execute("""
        UPDATE prison_jail pj
           SET active = FALSE,
               is_closed = FALSE
          FROM prison_jail parent
         WHERE pj.name = 'Pattukottai'
           AND pj.jail_type = 'sub_jail'
           AND pj.parent_id = parent.id
           AND parent.name ILIKE '%Chennai%';
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] deactivated wrong Pattukottai (Chennai) duplicate')


def _fix_cuddalore_women_subjail(cr):
    """
    The Cuddalore women_sub_jail under Vellore SPW was already closed before
    the GO-2025 migration — it is a different facility and should not appear
    in the general closed sub-jails list. It remains closed but its jail_type
    correctly distinguishes it; no change needed here.
    The count of 17 vs 14 includes this record because the closed-jails API
    returns all is_closed=True records regardless of jail_type.
    This migration is a no-op for that record — the API filter is fixed separately.
    """
    pass
