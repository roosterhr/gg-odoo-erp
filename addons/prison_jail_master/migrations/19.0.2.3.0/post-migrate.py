# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Applies GO-2025 closed sub-jail data:
#   - Marks 14 official closed sub-jails as is_closed=True
#   - Renames Portonovo @ Parangipettai → Portnovo at Parangipettai
#   - Deactivates S.J. Mettupalayam and S.J. Rasipuram (old-named duplicates)

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Run automatically by Odoo when prison_jail_master is upgraded to 19.0.2.3.0.
    Safe to re-run — idempotent.
    """
    _logger.info('[prison_jail_master] post-migrate 19.0.2.3.0 — start')

    _rename_portnovo(cr)
    _deactivate_old_sj_duplicates(cr)
    _mark_closed_subjails(cr)

    _logger.info('[prison_jail_master] post-migrate 19.0.2.3.0 — done')


def _rename_portnovo(cr):
    cr.execute("""
        UPDATE prison_jail
           SET name = 'Portnovo at Parangipettai'
         WHERE name = 'Portonovo @ Parangipettai';
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] renamed Portonovo @ Parangipettai → Portnovo at Parangipettai')


def _deactivate_old_sj_duplicates(cr):
    cr.execute("""
        UPDATE prison_jail
           SET active = FALSE
         WHERE name IN ('S.J. Mettupalayam', 'S.J. Rasipuram');
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] deactivated %d old S.J. duplicate records', cr.rowcount)


def _mark_closed_subjails(cr):
    closed_names = [
        'Madurantagam',
        'Mettupalayam',
        'Portnovo at Parangipettai',
        'Cuddalore',
        'Rasipuram',
        'Paramathivelur',
        'Manaparai',
        'Musiri',
        'Keeranur',
        'Thiruvadanai',
        'Pattukottai',
        'Arani',
        'Sattur',
        'Thiruchendur',
    ]
    cr.execute("""
        UPDATE prison_jail
           SET is_closed = TRUE,
               active    = TRUE
         WHERE name = ANY(%s)
           AND jail_type = 'sub_jail';
    """, (closed_names,))
    _logger.info('[prison_jail_master] marked %d sub-jails as closed per GO-2025', cr.rowcount)
