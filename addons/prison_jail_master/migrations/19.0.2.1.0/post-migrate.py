# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Odoo calls this automatically after the ORM has applied all schema changes
# (new columns and tables already exist when this runs).

import logging

_logger = logging.getLogger(__name__)

CLOSED_NAMES = [
    'madurantagam', 'arani', 'mettupalayam', 'parangipettai',
    'portnovo', 'cuddalore', 'rasipuram', 'paramathivelur',
    'paramathi velur', 'manaparai',
]


def migrate(cr, version):
    """
    Run automatically by Odoo when prison_jail_master is upgraded to 19.0.2.1.0.
    Safe to re-run — all operations are idempotent.
    """
    if not version:
        # Fresh install — data XML handles seeding, nothing to migrate.
        return

    _logger.info('[prison_jail_master] post-migrate 19.0.2.1.0 — start')

    _populate_hierarchy_type(cr)
    _flatten_sub_jails(cr)
    _mark_closed_jails(cr)
    _flag_closed_destination_transfers(cr)

    _logger.info('[prison_jail_master] post-migrate 19.0.2.1.0 — done')


# ── Step 1: populate hierarchy_type ──────────────────────────────────────────

def _populate_hierarchy_type(cr):
    # SPW → women, everything else → general (column default is already 'general')
    cr.execute("""
        UPDATE prison_jail
           SET hierarchy_type = 'women'
         WHERE jail_type = 'spw'
           AND (hierarchy_type IS NULL OR hierarchy_type = '');
    """)
    cr.execute("""
        UPDATE prison_jail
           SET hierarchy_type = 'general'
         WHERE jail_type != 'spw'
           AND (hierarchy_type IS NULL OR hierarchy_type = '');
    """)
    _logger.info('[prison_jail_master] hierarchy_type populated')


# ── Step 2: re-parent sub-jails directly under Central Prison ────────────────

def _flatten_sub_jails(cr):
    """
    Before v2, hierarchy was 3-level: Central → District → Sub.
    Flatten to 2-level: Central → Sub (skip District as parent).
    """
    cr.execute("""
        UPDATE prison_jail child
           SET parent_id = district.parent_id
          FROM prison_jail district
         WHERE child.parent_id = district.id
           AND district.jail_type = 'district_jail'
           AND district.parent_id IS NOT NULL
           AND child.jail_type NOT IN ('central_jail', 'spw');
    """)
    rows = cr.rowcount
    if rows:
        _logger.info('[prison_jail_master] re-parented %d child jails under Central Prison', rows)


# ── Step 3: mark known closed sub-jails ──────────────────────────────────────

def _mark_closed_jails(cr):
    for name_fragment in CLOSED_NAMES:
        cr.execute("""
            UPDATE prison_jail
               SET is_closed = true
             WHERE LOWER(name) LIKE %s
               AND is_closed = false;
        """, (f'%{name_fragment}%',))
        if cr.rowcount:
            _logger.info('[prison_jail_master] marked closed: %s (%d rows)', name_fragment, cr.rowcount)


# ── Step 4: flag pending transfers to now-closed prisons ─────────────────────

def _flag_closed_destination_transfers(cr):
    # tnpd_hr_employee may not be installed yet (no hard dependency from this module).
    # Skip gracefully if the table doesn't exist.
    cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
             WHERE table_name = 'x_transfer_approval_request'
        )
    """)
    if not cr.fetchone()[0]:
        _logger.info('[prison_jail_master] x_transfer_approval_request not found — skipping transfer flag step')
        return

    cr.execute("""
        UPDATE x_transfer_approval_request tar
           SET x_admin_remarks = COALESCE(x_admin_remarks, '') ||
               ' [MIGRATION] Destination prison is now closed — please revise.'
          FROM prison_jail pj
         WHERE tar.x_destination_prison_id = pj.id
           AND pj.is_closed = true
           AND tar.x_state NOT IN ('approved', 'rejected', 'cancelled', 'done')
           AND tar.x_admin_remarks NOT LIKE '%MIGRATION%';
    """)
    if cr.rowcount:
        _logger.info('[prison_jail_master] flagged %d pending transfers to closed prisons', cr.rowcount)
