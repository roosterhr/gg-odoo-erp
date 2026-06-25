# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3
#
# Role Standardization Migration API
# POST /api/admin/migrate/role-standardization
#
# Idempotent, transaction-safe migration that:
#   1. Merges alias role FK references in prison.designation.vacancy
#   2. Archives all non-official roles
#   3. Standardizes gender_type on the 6 canonical roles
#   4. Cleans x_designation text on hr.employee records
#
# Safe to run multiple times — repeated runs are no-ops.

import json
import logging

from odoo import http, SUPERUSER_ID
from odoo.http import request

_logger = logging.getLogger(__name__)

# ── Canonical role IDs (must exist in DB before running) ────────────────────
# These are the ONLY active roles after migration.
CANONICAL = {
    1: 'Jailer',
    2: 'Deputy Jailer',
    3: 'Assistant Jailer',
    4: 'Chief Head Warder',
    5: 'Grade I Warder',
    6: 'Grade II Warder',
}

# ── Merge map: canonical_id → [alias role IDs to absorb] ────────────────────
# Alias roles' designation vacancy records are re-assigned (or aggregated) to
# the canonical role, then the alias roles are archived.
MERGE_MAP = {
    1: [7, 111],               # Jailer Women, Jailor
    2: [8, 88],                # Deputy Jailer Women, Deputy Jailor
    3: [9, 73, 75],            # Assistant Jailer Women, Assistant Jailor, Asst. Jailor
    4: [10],                   # Chief Head Warder Women
    5: [11, 96, 97, 98, 99, 104, 105],  # Grade I Warder Women + First Grade Warder variants + Gr. I variants
    6: [12, 94, 95, 106, 107, 130, 131, 132, 133],  # Grade II Warder Women + Second Grade Warder variants + Gr. II (Female)
}

# ── Employee x_designation text → standardized name ─────────────────────────
DESIGNATION_MAP = {
    'Jailer (Men)':           'Jailer',
    'Jailer (Women)':         'Jailer',
    'Jailer (Male)':          'Jailer',
    'Jailer (Female)':        'Jailer',
    'Deputy Jailer (Men)':    'Deputy Jailer',
    'Deputy Jailer (Women)':  'Deputy Jailer',
    'Deputy Jailer (Male)':   'Deputy Jailer',
    'Deputy Jailer (Female)': 'Deputy Jailer',
    'Deputy Jailor':          'Deputy Jailer',
    'Assistant Jailer (Men)':    'Assistant Jailer',
    'Assistant Jailer (Women)':  'Assistant Jailer',
    'Assistant Jailer (Male)':   'Assistant Jailer',
    'Assistant Jailer (Female)': 'Assistant Jailer',
    'Assistant Jailor':          'Assistant Jailer',
    'Asst. Jailor':              'Assistant Jailer',
    'Chief Head Warder (Men)':    'Chief Head Warder',
    'Chief Head Warder (Women)':  'Chief Head Warder',
    'Chief Head Warder (Male)':   'Chief Head Warder',
    'Chief Head Warder (Female)': 'Chief Head Warder',
    'Grade I Warder (Men)':    'Grade I Warder',
    'Grade I Warder (Women)':  'Grade I Warder',
    'Grade I Warder (Male)':   'Grade I Warder',
    'Grade I Warder (Female)': 'Grade I Warder',
    'First Grade Warder':           'Grade I Warder',
    'First Grade Warder (Male)':    'Grade I Warder',
    'First Grade Warder (Female)':  'Grade I Warder',
    'Gr. I Warder (Male)':          'Grade I Warder',
    'Gr. I Warder (Female)':        'Grade I Warder',
    'Grade II Warder (Men)':    'Grade II Warder',
    'Grade II Warder (Women)':  'Grade II Warder',
    'Grade II Warder (Male)':   'Grade II Warder',
    'Grade II Warder (Female)': 'Grade II Warder',
    'Second Grade Warder':           'Grade II Warder',
    'Second Grade Warder (Male)':    'Grade II Warder',
    'Second Grade Warder (Female)':  'Grade II Warder',
    'Gr. II Warder (Female)':        'Grade II Warder',
    'Gr. II Warder (Male)':          'Grade II Warder',
    'Female Second Grade Warder':    'Grade II Warder',
}


class RoleMigrationAPI(http.Controller):

    def _json_response(self, data, status=200):
        origin = request.httprequest.headers.get('Origin', '*')
        return request.make_response(
            json.dumps(data, default=str),
            headers=[
                ('Content-Type',                    'application/json'),
                ('Access-Control-Allow-Origin',      origin),
                ('Access-Control-Allow-Credentials', 'true'),
                ('Access-Control-Allow-Headers',     'Content-Type, Authorization'),
                ('Access-Control-Allow-Methods',     'GET, POST, OPTIONS'),
            ],
            status=status,
        )

    def _ok(self, **kw):
        kw['success'] = True
        return self._json_response(kw)

    def _err(self, message, status=400):
        return self._json_response({'success': False, 'message': message}, status=status)

    # ── POST /api/admin/migrate/role-standardization ─────────────────────────

    @http.route(
        '/api/admin/migrate/role-standardization',
        auth='none', type='http', methods=['POST', 'OPTIONS'], csrf=False,
    )
    def migrate_roles(self, **_kw):
        """
        Idempotent role standardization migration.

        Auth: requires active Odoo admin session.

        Returns JSON summary of every change applied.
        """
        # OPTIONS pre-flight
        if request.httprequest.method == 'OPTIONS':
            return self._json_response({})

        uid = request.session.uid
        if not uid:
            return self._err('Authentication required', status=401)

        su_env = request.env(user=SUPERUSER_ID)
        user = su_env['res.users'].browse(uid)
        if not user.exists() or not user._is_admin():
            return self._err('Admin access required', status=403)

        cr = su_env.cr

        try:
            result = self._run_migration(cr, su_env)
            _logger.info('role-standardization migration complete: %s', result)
            return self._ok(**result)
        except Exception as exc:
            cr.rollback()
            _logger.exception('role-standardization migration FAILED')
            return self._json_response(
                {'success': False, 'message': str(exc)}, status=500
            )

    # ── Migration core ────────────────────────────────────────────────────────

    def _run_migration(self, cr, env):
        """Execute all migration steps inside the current transaction."""

        stats = {
            'merged_roles':       0,
            'archived_roles':     0,
            'updated_vacancies':  0,
            'deleted_vacancies':  0,
            'updated_employees':  0,
        }

        # ── Step 1: Verify canonical roles exist ──────────────────────────────
        canonical_ids = tuple(CANONICAL.keys())
        cr.execute(
            'SELECT id, name FROM prison_role WHERE id = ANY(%s)',
            [list(canonical_ids)],
        )
        found = {row[0]: row[1] for row in cr.fetchall()}
        missing = [cid for cid in canonical_ids if cid not in found]
        if missing:
            raise ValueError(
                f'Canonical role IDs not found in DB: {missing}. '
                'Aborting — check that seed data is loaded.'
            )

        # ── Step 2: Merge alias roles into canonical ──────────────────────────
        all_alias_ids = []
        for canonical_id, alias_ids in MERGE_MAP.items():
            for alias_id in alias_ids:
                self._merge_alias(cr, canonical_id, alias_id, stats)
                all_alias_ids.append(alias_id)

        # ── Step 3: Archive all non-canonical roles ───────────────────────────
        # Includes aliases (already archived in step 2 individually) +
        # all other non-official roles.
        cr.execute(
            """
            UPDATE prison_role
            SET    active = false
            WHERE  id NOT IN %s
            AND    active = true
            RETURNING id
            """,
            [canonical_ids],
        )
        newly_archived = cr.rowcount
        stats['archived_roles'] += newly_archived
        _logger.info('Archived %d non-canonical roles', newly_archived)

        # ── Step 4: Set canonical roles to active + gender_type=both ─────────
        cr.execute(
            """
            UPDATE prison_role
            SET    active      = true,
                   gender_type = 'both'
            WHERE  id IN %s
            """,
            [canonical_ids],
        )

        # ── Step 5: Recompute stored fields on designation vacancy ────────────
        # vacancy_count = sanctioned_strength - filled_strength (stored computed)
        cr.execute(
            """
            UPDATE prison_designation_vacancy
            SET    vacancy_count = GREATEST(0, sanctioned_strength - filled_strength),
                   role_name     = pr.name
            FROM   prison_role pr
            WHERE  prison_designation_vacancy.role_id = pr.id
            """
        )

        # ── Step 6: Standardize hr.employee x_designation text ───────────────
        emp_updated = 0
        for old_name, new_name in DESIGNATION_MAP.items():
            cr.execute(
                """
                UPDATE hr_employee
                SET    x_designation = %s
                WHERE  x_designation = %s
                """,
                [new_name, old_name],
            )
            emp_updated += cr.rowcount

        stats['updated_employees'] = emp_updated

        # ── Step 7: Validate — confirm only 6 active roles remain ─────────────
        cr.execute('SELECT COUNT(*) FROM prison_role WHERE active = true')
        active_count = cr.fetchone()[0]
        if active_count != 6:
            raise ValueError(
                f'Validation failed: expected 6 active roles, found {active_count}. '
                'Rolling back.'
            )

        stats['active_role_count'] = active_count
        return stats

    def _merge_alias(self, cr, canonical_id, alias_id, stats):
        """
        Re-assign designation vacancy records from alias_id to canonical_id.

        Conflict (same prison already has a canonical record): aggregate counts
        into the canonical record and delete the alias record.

        No conflict: update alias record's role_id to canonical.
        """
        # Check alias role exists (may already be archived from a prior run)
        cr.execute('SELECT id FROM prison_role WHERE id = %s', [alias_id])
        if not cr.fetchone():
            _logger.debug('Alias role %d not found — skipping', alias_id)
            return

        # 1. Aggregate conflicting records into canonical, then delete alias
        cr.execute(
            """
            UPDATE prison_designation_vacancy AS dst
            SET    sanctioned_strength = dst.sanctioned_strength + src.sanctioned_strength,
                   filled_strength     = dst.filled_strength     + src.filled_strength
            FROM   prison_designation_vacancy src
            WHERE  dst.role_id    = %s
            AND    src.role_id    = %s
            AND    dst.prison_id  = src.prison_id
            """,
            [canonical_id, alias_id],
        )

        cr.execute(
            """
            DELETE FROM prison_designation_vacancy
            WHERE  role_id = %s
            AND    prison_id IN (
                SELECT prison_id FROM prison_designation_vacancy WHERE role_id = %s
            )
            """,
            [alias_id, canonical_id],
        )
        deleted = cr.rowcount
        stats['deleted_vacancies'] += deleted

        # 2. Move non-conflicting alias records to canonical
        cr.execute(
            """
            UPDATE prison_designation_vacancy
            SET    role_id = %s
            WHERE  role_id = %s
            """,
            [canonical_id, alias_id],
        )
        moved = cr.rowcount
        stats['updated_vacancies'] += moved

        # 3. Archive alias role
        cr.execute(
            'UPDATE prison_role SET active = false WHERE id = %s',
            [alias_id],
        )
        stats['merged_roles'] += 1

        if deleted or moved:
            _logger.info(
                'Merged alias role %d → canonical %d | moved=%d deleted=%d',
                alias_id, canonical_id, moved, deleted,
            )
