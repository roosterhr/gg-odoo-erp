# Part of TNPD Prison Management System.
# License: LGPL-3
"""
Prison Hierarchy v2 Migration Script
======================================
Flattens the 3-level (Central → District → Sub) hierarchy to 2-level
(Central/SPW → all children directly).

Usage (Odoo shell):
    from addons.prison_jail_master.scripts.migrate_hierarchy_v2 import migrate, get_report
    migrate(env)
    print(get_report(env))

Or via Odoo migration hook — add to a dated migration file:
    from odoo.addons.prison_jail_master.scripts.migrate_hierarchy_v2 import migrate
    def migrate(cr, version):
        env = api.Environment(cr, SUPERUSER_ID, {})
        from odoo.addons.prison_jail_master.scripts.migrate_hierarchy_v2 import migrate as run
        run(env)

All changes use ORM write() so _parent_store and computed stored fields
recompute automatically.  The script is idempotent — safe to run multiple times.
"""

import logging

_logger = logging.getLogger(__name__)

# ── Closed sub-jail name fragments (case-insensitive ilike search) ─────────────
CLOSED_JAIL_NAMES = [
    'Madurantagam',
    'Arani',
    'Mettupalayam',
    'Parangipettai',
    'Cuddalore',
    'Rasipuram',
    'Paramathivelur',
    'Manaparai',
]

# ── jail_type → prison.vacancy prison_type map ────────────────────────────────
JAIL_TO_VACANCY_TYPE = {
    'central_jail':    'central_prison',
    'spw':             'spw',
    'district_jail':   'district_jail',
    'sub_jail':        'sub_jail',
    'women_sub_jail':  'women_sub_jail',
    'special_sub_jail': 'special_sub_jail',
    'open_air_jail':   'open_air_jail',
    'farm_jail':       'farm_jail',
    'transit_yard':    'transit_yard',
}


def migrate(env):
    """Run all migration steps in sequence. Wrap the call in a savepoint."""
    _logger.info('=== Prison Hierarchy v2 Migration START ===')

    counts = {
        'hierarchy_type_set': 0,
        'spw_converted': 0,
        'sub_jails_reparented': 0,
        'district_jails_verified': 0,
        'duplicates_merged': 0,
        'closed_marked': 0,
        'transfers_flagged': 0,
    }

    Jail = env['prison.jail'].sudo()

    # ── Step 1: Populate hierarchy_type ──────────────────────────────────────
    _logger.info('Step 1: Setting hierarchy_type on existing records')

    # Central Jails that are Women prisons → convert to spw + women hierarchy
    central_all = Jail.search([('jail_type', '=', 'central_jail')])
    for rec in central_all:
        name_lower = rec.name.lower()
        if 'special' in name_lower and 'women' in name_lower:
            rec.write({'jail_type': 'spw', 'hierarchy_type': 'women'})
            counts['spw_converted'] += 1
            _logger.info('  Converted to SPW: %s', rec.name)
        elif rec.hierarchy_type != 'general':
            rec.write({'hierarchy_type': 'general'})
            counts['hierarchy_type_set'] += 1

    # District Jails → general
    dj_recs = Jail.search([
        ('jail_type', '=', 'district_jail'),
        ('hierarchy_type', '!=', 'general'),
    ])
    if dj_recs:
        dj_recs.write({'hierarchy_type': 'general'})
        counts['hierarchy_type_set'] += len(dj_recs)

    # Sub Jails → general (women sub-jails will be handled separately when imported)
    sj_recs = Jail.search([
        ('jail_type', '=', 'sub_jail'),
        ('hierarchy_type', '!=', 'general'),
    ])
    if sj_recs:
        sj_recs.write({'hierarchy_type': 'general'})
        counts['hierarchy_type_set'] += len(sj_recs)

    _logger.info('  hierarchy_type set on %d records', counts['hierarchy_type_set'])

    # ── Step 2: Re-parent sub-jails that sit under district jails ────────────
    _logger.info('Step 2: Re-parenting sub-jails from district jails to central prisons')

    sub_under_district = Jail.search([
        ('jail_type', '=', 'sub_jail'),
        ('parent_id.jail_type', '=', 'district_jail'),
    ])

    for sj in sub_under_district:
        district = sj.parent_id
        grandparent = district.parent_id

        if grandparent and grandparent.jail_type in ('central_jail', 'spw'):
            sj.write({'parent_id': grandparent.id})
            counts['sub_jails_reparented'] += 1
            _logger.info(
                '  Re-parented "%s": %s → %s',
                sj.name, district.name, grandparent.name,
            )
        else:
            # No grandparent — find a central prison in the same district
            cp = Jail.search([
                ('jail_type', 'in', ['central_jail', 'spw']),
                ('district', '=', sj.district or district.district),
                ('active', '=', True),
            ], limit=1)
            if cp:
                sj.write({'parent_id': cp.id})
                counts['sub_jails_reparented'] += 1
                _logger.info(
                    '  Re-parented "%s" (no grandparent) → %s (district match)',
                    sj.name, cp.name,
                )
            else:
                _logger.warning(
                    '  SKIPPED "%s" — no central prison found for district "%s"',
                    sj.name, sj.district or district.district,
                )

    # ── Step 3: Verify district jails are direct children of central prisons ─
    _logger.info('Step 3: Verifying district jail parentage')

    orphan_dj = Jail.search([
        ('jail_type', '=', 'district_jail'),
        ('parent_id', '=', False),
        ('active', '=', True),
    ])
    for dj in orphan_dj:
        cp = Jail.search([
            ('jail_type', 'in', ['central_jail', 'spw']),
            ('district', '=', dj.district),
            ('active', '=', True),
        ], limit=1)
        if cp:
            dj.write({'parent_id': cp.id})
            counts['district_jails_verified'] += 1
            _logger.info('  Assigned orphan DJ "%s" → %s', dj.name, cp.name)
        else:
            _logger.warning('  Orphan DJ "%s" — no central prison found', dj.name)

    # ── Step 4: Deduplicate central prisons / SPW ────────────────────────────
    _logger.info('Step 4: Deduplicating parent institutions')

    Vacancy = env['prison.vacancy'].sudo()

    for jail_type in ('central_jail', 'spw'):
        all_parents = Jail.search(
            [('jail_type', '=', jail_type), ('active', 'in', [True, False])],
            order='id asc',
        )
        seen_names = {}
        for rec in all_parents:
            key = rec.name.strip().lower()
            if key not in seen_names:
                seen_names[key] = rec
            else:
                canonical = seen_names[key]
                _logger.info(
                    '  Merging duplicate "%s" (id=%d) → canonical id=%d',
                    rec.name, rec.id, canonical.id,
                )
                # Re-parent children
                dup_children = Jail.search([
                    ('parent_id', '=', rec.id),
                    ('active', 'in', [True, False]),
                ])
                if dup_children:
                    dup_children.write({'parent_id': canonical.id})

                # Re-map vacancy
                dup_vacancy = Vacancy.search([('prison_id', '=', rec.id)])
                if dup_vacancy:
                    canonical_vacancy = Vacancy.search([('prison_id', '=', canonical.id)], limit=1)
                    if canonical_vacancy:
                        # Merge vacancy counts
                        canonical_vacancy.write({
                            'sanctioned_strength': canonical_vacancy.sanctioned_strength + dup_vacancy.sanctioned_strength,
                            'occupied_count': canonical_vacancy.occupied_count + dup_vacancy.occupied_count,
                            'vacancy_count': canonical_vacancy.vacancy_count + dup_vacancy.vacancy_count,
                        })
                        dup_vacancy.write({'active': False})
                    else:
                        dup_vacancy.write({'prison_id': canonical.id})

                # Soft-delete the duplicate
                rec.write({'active': False})
                counts['duplicates_merged'] += 1

    # ── Step 5: Mark closed sub-jails ────────────────────────────────────────
    _logger.info('Step 5: Marking closed sub-jails')

    for name_fragment in CLOSED_JAIL_NAMES:
        matches = Jail.search([
            ('name', 'ilike', name_fragment),
            ('jail_type', 'in', ['sub_jail', 'women_sub_jail', 'special_sub_jail']),
            ('active', '=', True),
        ])
        for rec in matches:
            if not rec.is_closed:
                rec.write({
                    'is_closed': True,
                    'closed_remarks': 'Operationally closed per TNPD hierarchy review 2025.',
                })
                counts['closed_marked'] += 1
                _logger.info('  Marked closed: %s', rec.name)

    # ── Step 6: Flag pending transfers to closed sub-jails ───────────────────
    _logger.info('Step 6: Flagging pending transfer requests to closed institutions')

    MIGRATION_NOTE = (
        '\n[MIGRATION NOTE] Destination prison is now closed. Please revise.'
    )

    try:
        Transfer = env['transfer.approval.request'].sudo()
        pending_to_closed = Transfer.search([
            ('destination_prison_id.is_closed', '=', True),
            ('state', 'not in', ['approved', 'rejected', 'cancelled', 'done']),
        ])
        for tr in pending_to_closed:
            existing = tr.admin_remarks or ''
            if '[MIGRATION NOTE]' not in existing:
                tr.write({'admin_remarks': existing + MIGRATION_NOTE})
                counts['transfers_flagged'] += 1
                _logger.info(
                    '  Flagged transfer id=%d → %s',
                    tr.id, tr.destination_prison_id.name,
                )
    except Exception as exc:
        _logger.warning('Step 6 skipped — transfer model unavailable: %s', exc)

    _logger.info('=== Prison Hierarchy v2 Migration COMPLETE ===')
    _logger.info('Summary: %s', counts)
    return counts


def get_report(env):
    """Return a summary of current hierarchy state post-migration."""
    Jail = env['prison.jail'].sudo()

    lines = ['Prison Hierarchy v2 — Post-Migration Report', '=' * 50]

    for jt, label in [
        ('central_jail',    'Central Prisons (General)'),
        ('spw',             'Special Prisons for Women'),
        ('district_jail',   'District Jails'),
        ('sub_jail',        'Sub-Jails'),
        ('women_sub_jail',  'Women Sub-Jails'),
        ('special_sub_jail', 'Special Sub-Jails'),
        ('open_air_jail',   'Open Air Jails'),
        ('farm_jail',       'Farm Jails'),
        ('transit_yard',    'Transit Yards'),
    ]:
        count = Jail.search_count([('jail_type', '=', jt), ('active', '=', True)])
        lines.append(f'{label:<35}: {count}')

    closed = Jail.search_count([('is_closed', '=', True), ('active', '=', True)])
    orphan_children = Jail.search_count([
        ('jail_type', 'not in', ['central_jail', 'spw']),
        ('parent_id', '=', False),
        ('active', '=', True),
    ])

    lines.append('-' * 50)
    lines.append(f'{"Closed institutions":<35}: {closed}')
    lines.append(f'{"Orphaned children (no parent)":<35}: {orphan_children}')

    if orphan_children:
        orphans = Jail.search([
            ('jail_type', 'not in', ['central_jail', 'spw']),
            ('parent_id', '=', False),
            ('active', '=', True),
        ])
        lines.append('  Orphans:')
        for o in orphans:
            lines.append(f'    - [{o.jail_type}] {o.name} (district: {o.district or "?"})')

    return '\n'.join(lines)
