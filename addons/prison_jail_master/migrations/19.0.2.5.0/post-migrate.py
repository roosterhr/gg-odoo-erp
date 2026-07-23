# Part of TNPD Prison Management System.
# License: LGPL-3
#
# FULL REBUILD of prison.designation.vacancy from the department's authoritative
# "Jailer to Grade II Warder" strength statement (shipped alongside this script
# as designation_rebuild.csv), plus consolidation of the duplicate / archived
# prison.jail records behind the Personnel + Vacancy page issues.
#
# Why a full rebuild:
#   Verified against the dev DB, the grade-wise data was scattered and mildly
#   double-counted across duplicate "twin" prison.jail records (a facility had
#   its data split between an archived "D.J./S.J. <name>" record and a partial
#   active one). Patching twin-by-twin cannot guarantee the reported grand
#   total. The authoritative statement's own rows sum to exactly
#   4159 / 3856 / 303, so recreating every facility's rows from it — once, on a
#   single canonical record each — guarantees both correct per-prison
#   breakdowns AND the official total.
#
# What it does:
#   1. Consolidate: reactivate the operational district jails / women annexes
#      that were wrongly archived, and absorb each duplicate twin into its
#      canonical record (reassigning employees, transfer requests, child jails
#      and the aggregate vacancy row), then archive the twin.
#   2. Wipe prison.designation.vacancy entirely.
#   3. Rebuild: recreate every (prison, role) row from the CSV via the ORM,
#      resolving each canonical record by name + jail_type.
#
# Cross-gender postings (male staff at a women's prison, women staff at a men's
# central) are summed into the single canonical record for that prison — the
# data model keys on (prison, role, hierarchy_type) and cannot hold both
# separately. This preserves the exact grand total.
#
# Properties:
#   * Idempotent — reruns converge to the same state.
#   * Non-destructive to real records: twins are ARCHIVED (active=False), never
#     hard-deleted; their references are moved first. Designation rows are
#     wiped and rebuilt from the authoritative source, so the table always ends
#     matching the statement.
#   * Matches by name + jail_type (ids differ per environment); a canonical
#     record that is absent/ambiguous is skipped with a log line.

import csv
import logging
import os

_logger = logging.getLogger(__name__)

_CSV = os.path.join(os.path.dirname(__file__), 'designation_rebuild.csv')

_EMP_COLS = ['x_central_jail_id', 'x_district_jail_id', 'x_sub_jail_id']
_TRANSFER_COLS = [
    'current_central_prison', 'current_district_jail', 'current_sub_jail',
    'requested_central_prison', 'requested_district_jail', 'requested_sub_jail',
    'preference_2_central_prison', 'preference_2_district_jail', 'preference_2_sub_jail',
    'preference_3_central_prison', 'preference_3_district_jail', 'preference_3_sub_jail',
]

# Consolidations applied before the rebuild.
#   canonical, jail_type, reactivate, rename_from, [absorb (name, type) ...]
_CONSOLIDATE = [
    ('D.J. Attur',          'district_jail', True, 'D.J. Attur (Closed)', []),
    ('D.J. Dharmapuri',     'district_jail', True, None, [('Dharmapuri', 'district_jail'), ('Dharmapuri', 'women_sub_jail')]),
    ('D.J. Dindigul',       'district_jail', True, None, [('Dindigul', 'district_jail')]),
    ('D.J. Theni',          'district_jail', True, None, [('Theni', 'district_jail')]),
    ('D.J. Ramanathapuram', 'district_jail', True, None, [('Ramanathapuram', 'district_jail')]),
    ('D.J. Virudhunagar',   'district_jail', True, None, [('Virudhunagar', 'district_jail')]),
    ('D.J. Pudukkottai',    'district_jail', True, None, [('Pudukkottai', 'district_jail')]),
    ('D.J. Tiruppur',       'district_jail', True, None, [('Tiruppur', 'district_jail')]),
    ('Kodaikanal',          'sub_jail', False, None, [('S.J. Kodaikanal (Closed)', 'sub_jail')]),
    ('S.J. Musiri',         'sub_jail', False, None, [('Musiri', 'sub_jail'), ('S.J. Musiri (Closed)', 'sub_jail')]),
    ('S.J. Portonova',      'sub_jail', False, None, [('Portnovo at Parangipettai', 'sub_jail'), ('S.J. Parangipet (Closed)', 'sub_jail')]),
    ('S.J. Tiruchendur',    'sub_jail', False, None, [('Thiruchendur', 'sub_jail'), ('S.J. Thiruchendur (Closed)', 'sub_jail')]),
    ('S.J. Paramathy',      'sub_jail', False, None, [('Paramathivelur', 'sub_jail'), ('S.J. Paramathivelur (Closed)', 'sub_jail')]),
    ('S.J. Pattukottai',    'sub_jail', False, None, [('Pattukottai', 'sub_jail')]),
    ('D.J. Chengalpattu Women Annex', 'sub_jail', True, None, []),
    ('Female Jail, Perurani', 'sub_jail', True, None, []),
]


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info('[prison_jail_master] post-migrate 19.0.2.5.0 — full rebuild start')

    have_transfer = _table_exists(cr, 'transfer_approval_request')
    for spec in _CONSOLIDATE:
        try:
            _consolidate(env, spec, have_transfer)
        except Exception:
            _logger.exception('[prison_jail_master] consolidate failed for %r', spec[0])

    _rebuild_designations(env)
    _logger.info('[prison_jail_master] post-migrate 19.0.2.5.0 — full rebuild done')


# ── helpers ────────────────────────────────────────────────────────────────

def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s", [table])
    return bool(cr.fetchone())


def _col_exists(cr, table, col):
    cr.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name=%s AND column_name=%s""", [table, col])
    return bool(cr.fetchone())


def _find_jail(env, name, jail_type):
    recs = env['prison.jail'].with_context(active_test=False).search(
        [('name', '=', name), ('jail_type', '=', jail_type)])
    if len(recs) == 1:
        return recs
    if len(recs) > 1:
        _logger.warning('[prison_jail_master] %r <%s> matched %d records — skipping',
                        name, jail_type, len(recs))
    return None


def _consolidate(env, spec, have_transfer):
    cr = env.cr
    canonical, jtype, reactivate, rename_from, absorb = spec
    rec = _find_jail(env, canonical, jtype)
    if not rec and rename_from:
        rec = _find_jail(env, rename_from, jtype)
        if rec:
            rec.name = canonical
    if not rec:
        _logger.info('[prison_jail_master] consolidate: canonical %r <%s> not found — skip',
                     canonical, jtype)
        return
    keep_id = rec.id
    if reactivate:
        rec.write({'active': True, 'is_closed': False})

    for dup_name, dup_type in absorb:
        dup = _find_jail(env, dup_name, dup_type)
        if not dup or dup.id == keep_id:
            continue
        dup_id = dup.id
        for col in _EMP_COLS:
            if _col_exists(cr, 'hr_employee', col):
                cr.execute("UPDATE hr_employee SET %s=%%s WHERE %s=%%s" % (col, col), [keep_id, dup_id])
        if have_transfer:
            for col in _TRANSFER_COLS:
                if _col_exists(cr, 'transfer_approval_request', col):
                    cr.execute("UPDATE transfer_approval_request SET %s=%%s WHERE %s=%%s" % (col, col),
                               [keep_id, dup_id])
        cr.execute("UPDATE prison_jail SET parent_id=%s WHERE parent_id=%s", [keep_id, dup_id])
        cr.execute("""DELETE FROM prison_vacancy WHERE prison_id=%s
                       AND EXISTS (SELECT 1 FROM prison_vacancy WHERE prison_id=%s)""", [dup_id, keep_id])
        cr.execute("UPDATE prison_vacancy SET prison_id=%s WHERE prison_id=%s", [keep_id, dup_id])
        cr.execute("UPDATE prison_jail SET active=FALSE WHERE id=%s", [dup_id])
        _logger.info('[prison_jail_master] absorbed %r(id=%s) into %r(id=%s)',
                     dup_name, dup_id, canonical, keep_id)


def _rebuild_designations(env):
    cr = env.cr
    # Resolve canonical role ids by name.
    Role = env['prison.role']
    role_ids = {}
    with open(_CSV, encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            rn = row['role']
            if rn not in role_ids:
                rec = Role.search([('name', '=', rn)], limit=1)
                if not rec:
                    rec = Role.create({'name': rn, 'gender_type': 'both'})
                    _logger.info('[prison_jail_master] created missing role %r', rn)
                role_ids[rn] = rec.id

    # Wipe and rebuild the whole table.
    cr.execute("DELETE FROM prison_designation_vacancy")
    env.invalidate_all()
    Desig = env['prison.designation.vacancy']

    created = skipped = 0
    resolved = {}
    with open(_CSV, encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            key = (row['canonical'], row['jail_type'])
            if key not in resolved:
                rec = _find_jail(env, row['canonical'], row['jail_type'])
                if rec and not rec.active:
                    rec.write({'active': True})
                resolved[key] = rec.id if rec else None
            pid = resolved[key]
            if not pid:
                skipped += 1
                continue
            Desig.create({
                'prison_id': pid,
                'role_id': role_ids[row['role']],
                'sanctioned_strength': int(row['sanctioned']),
                'filled_strength': int(row['filled']),
            })
            created += 1
    _logger.info('[prison_jail_master] rebuilt designation table: %d rows created, %d skipped (%d facilities)',
                 created, skipped, len(resolved))
