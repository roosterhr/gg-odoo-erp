# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Prison Vacancy REST API
=======================

Endpoints
---------
GET  /api/vacancy/roles
    List all prison roles from Role Master.
    Optional ?gender_type=men|women|both filter.

GET  /api/vacancy/designation
    Designation-wise vacancy for a prison.
    Requires: ?prison_id=<int>
    Optional: ?role_id=<int>

GET  /api/vacancy/dashboard
    System-wide vacancy summary (prison-level + top-role vacancies).

POST /api/transfer/check-availability
    Check total prison vacancy (backward-compat).

POST /api/transfer/check-role-availability
    Check vacancy for a specific prison + role combination.
    Request: { "prison_id": <int>, "role_id": <int> }

POST /api/vacancy/import
    Bulk-upsert prison-level vacancy records.

POST /api/vacancy/import-csv
    Import designation vacancies from CSV text.
    Request: { "csv": "<csv content string>" }

POST /api/vacancy/update
    Update a single prison's aggregate vacancy figures.
"""

import csv
import io
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


_CANONICAL_ROLE_NAMES = [
    'Jailer', 'Deputy Jailer', 'Assistant Jailer',
    'Chief Head Warder', 'Grade I Warder', 'Grade II Warder',
]


def _clear_canonical_roles(env):
    """Delete all designation.vacancy records for the 6 canonical executive roles."""
    roles = env['prison.role'].sudo().search([('name', 'in', _CANONICAL_ROLE_NAMES)])
    recs = env['prison.designation.vacancy'].sudo().search([('role_id', 'in', roles.ids)])
    recs.unlink()
    env.cr.commit()


class VacancyApiController(http.Controller):

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _ok(self, data):
        return self._json({'success': True, **data})

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status=status)

    def _parse_body(self):
        try:
            body = request.httprequest.get_data(as_text=True)
            return json.loads(body) if body else {}
        except Exception:
            return None

    def _int(self, value, default=None):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ── GET /api/vacancy/roles ────────────────────────────────────────────────

    @http.route(
        '/api/vacancy/roles',
        type='http',
        auth='none',
        methods=['GET'],
        csrf=False,
    )
    def get_roles(self, **kwargs):
        gender_type = kwargs.get('gender_type')
        domain = [('active', '=', True)]
        if gender_type in ('men', 'women', 'both'):
            domain.append(('gender_type', '=', gender_type))

        roles = request.env['prison.role'].sudo().search(domain, order='sequence, name')
        return self._ok({
            'roles': [
                {
                    'id':          r.id,
                    'name':        r.name,
                    'gender_type': r.gender_type,
                }
                for r in roles
            ],
            'total': len(roles),
        })

    # ── GET /api/vacancy/designation ─────────────────────────────────────────

    @http.route(
        '/api/vacancy/designation',
        type='http',
        auth='none',
        methods=['GET'],
        csrf=False,
    )
    def get_designation_vacancy(self, **kwargs):
        prison_id = self._int(kwargs.get('prison_id'))
        if not prison_id:
            return self._err('prison_id is required.')

        domain = [('prison_id', '=', prison_id), ('role_id.active', '=', True)]
        role_id = self._int(kwargs.get('role_id'))
        if role_id:
            domain.append(('role_id', '=', role_id))

        recs = request.env['prison.designation.vacancy'].sudo().search(domain)
        records = [r.as_api_dict() for r in recs]

        # Fallback: when no designation records exist, return aggregate from prison.vacancy
        if not records:
            pv = request.env['prison.vacancy'].sudo().search(
                [('prison_id', '=', prison_id)], limit=1)
            if pv and pv.sanctioned_strength:
                jail = request.env['prison.jail'].sudo().browse(prison_id)
                records = [{
                    'role_id':            0,
                    'role_name':          'Total Strength',
                    'prison_id':          prison_id,
                    'prison_name':        jail.name if jail.exists() else '',
                    'sanctioned_strength': pv.sanctioned_strength,
                    'filled_strength':    pv.occupied_count,
                    'vacancy_count':      pv.vacancy_count,
                    'gender_type':        'both',
                }]

        return self._ok({
            'prison_id': prison_id,
            'records': records,
            'total': len(records),
        })

    # ── GET /api/vacancy/dashboard ────────────────────────────────────────────

    @http.route(
        '/api/vacancy/dashboard',
        type='http',
        auth='none',
        methods=['GET'],
        csrf=False,
    )
    def get_dashboard(self, **kwargs):
        # Canonical role IDs: the only 6 official roles
        CANONICAL_IDS = (1, 2, 3, 4, 5, 6)

        # Stat cards — totals scoped to the 6 canonical roles only
        request.env.cr.execute("""
            SELECT
                SUM(dv.sanctioned_strength) AS sanctioned,
                SUM(dv.filled_strength)     AS filled,
                SUM(dv.sanctioned_strength) - SUM(dv.filled_strength) AS vacancy,
                COUNT(DISTINCT dv.prison_id) AS prison_count
              FROM prison_designation_vacancy dv
             WHERE dv.role_id IN %s
        """, [CANONICAL_IDS])
        row = request.env.cr.fetchone()
        total_sanctioned = int(row[0] or 0)
        total_filled     = int(row[1] or 0)
        total_vacancy    = int(row[2] or 0)
        prison_count     = int(row[3] or 0)

        def _role_rows(hierarchy_filter=None):
            sql = """
                SELECT r.name AS role_name, r.gender_type,
                       SUM(dv.sanctioned_strength) AS sanctioned,
                       SUM(dv.filled_strength) AS filled,
                       SUM(dv.sanctioned_strength) - SUM(dv.filled_strength) AS vacancy
                  FROM prison_designation_vacancy dv
                  JOIN prison_role r ON r.id = dv.role_id
                 WHERE dv.role_id IN %s
            """
            params = [CANONICAL_IDS]
            if hierarchy_filter:
                sql += " AND dv.hierarchy_type = %s"
                params.append(hierarchy_filter)
            sql += " GROUP BY r.id, r.name, r.gender_type ORDER BY vacancy DESC"
            request.env.cr.execute(sql, params)
            return [
                {
                    'role_name':  row[0],
                    'gender_type': row[1],
                    'sanctioned': int(row[2] or 0),
                    'filled':     int(row[3] or 0),
                    'vacancy':    int(row[4] or 0),
                }
                for row in request.env.cr.fetchall()
            ]

        return self._ok({
            'prison_summary': {
                'total_sanctioned': total_sanctioned,
                'total_filled':     total_filled,
                'total_vacancy':    total_vacancy,
                'prison_count':     prison_count,
            },
            'role_summary':       _role_rows(),
            'role_summary_men':   _role_rows('general'),
            'role_summary_women': _role_rows('women'),
        })

    # ── POST /api/transfer/check-availability (backward-compat) ──────────────

    @http.route(
        '/api/transfer/check-availability',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def check_availability(self, **kwargs):
        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')

        prison_id = self._int(body.get('prison_id'))
        if not prison_id:
            return self._err('prison_id is required and must be an integer.')

        vacancy = request.env['prison.vacancy'].sudo().search(
            [('prison_id', '=', prison_id), ('active', '=', True)],
            limit=1,
        )
        if not vacancy:
            return self._err(f'No vacancy record found for prison_id {prison_id}.', status=404)

        available = vacancy.is_vacancy_available()
        return self._ok({
            'vacancy_available': available,
            'prison_id':         prison_id,
            'prison_name':       vacancy.prison_name,
            'sanctioned_strength': vacancy.sanctioned_strength,
            'occupied_count':    vacancy.occupied_count,
            'vacancy_count':     vacancy.vacancy_count,
            'message': (
                'Vacancy available. Transfer can be processed.'
                if available else
                'No vacancy available in requested prison.'
            ),
        })

    # ── GET /api/vacancy/check ────────────────────────────────────────────────
    # GET version: ?prison_id=<int>&role_id=<int>  — auth='none', session optional

    @http.route(
        '/api/vacancy/check',
        type='http',
        auth='none',
        methods=['GET'],
        csrf=False,
    )
    def check_vacancy_get(self, **kwargs):
        prison_id = self._int(kwargs.get('prison_id'))
        role_id   = self._int(kwargs.get('role_id'))
        if not prison_id:
            return self._err('prison_id is required.')
        if not role_id:
            return self._err('role_id is required.')
        return self._role_vacancy_response(prison_id, role_id)

    # ── POST /api/transfer/check-role-availability ────────────────────────────

    @http.route(
        '/api/transfer/check-role-availability',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def check_role_availability(self, **kwargs):
        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')
        prison_id = self._int(body.get('prison_id'))
        role_id   = self._int(body.get('role_id'))
        if not prison_id:
            return self._err('prison_id is required.')
        if not role_id:
            return self._err('role_id is required.')
        return self._role_vacancy_response(prison_id, role_id)

    def _role_vacancy_response(self, prison_id, role_id):
        desig = request.env['prison.designation.vacancy'].sudo().search([
            ('prison_id', '=', prison_id),
            ('role_id', '=', role_id),
        ], limit=1)

        if not desig:
            jail = request.env['prison.jail'].sudo().browse(prison_id)
            prison_name = jail.name if jail.exists() else f'ID {prison_id}'
            role = request.env['prison.role'].sudo().browse(role_id)
            role_name = role.name if role.exists() else f'ID {role_id}'
            return self._ok({
                'vacancy_available': False,
                'prison_id':    prison_id,
                'prison_name':  prison_name,
                'role_id':      role_id,
                'role_name':    role_name,
                'vacancy_count': 0,
                'sanctioned_strength': 0,
                'filled_strength': 0,
                'message': f'No vacancy record found for {role_name} in {prison_name}.',
            })

        available = desig.is_vacancy_available()
        return self._ok({
            'vacancy_available':   available,
            'prison_id':           prison_id,
            'prison_name':         desig.prison_name,
            'role_id':             role_id,
            'role_name':           desig.role_name,
            'sanctioned_strength': desig.sanctioned_strength,
            'filled_strength':     desig.filled_strength,
            'vacancy_count':       desig.vacancy_count,
            'message': (
                f'Vacancy available for {desig.role_name}.'
                if available else
                f'No vacancy available for {desig.role_name} in {desig.prison_name}.'
            ),
        })

    # ── POST /api/vacancy/import ───────────────────────────────────────────────

    @http.route(
        '/api/vacancy/import',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def vacancy_import(self, **kwargs):
        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')

        records = body.get('records')
        if not isinstance(records, list) or not records:
            return self._err('"records" must be a non-empty list.')

        Vacancy = request.env['prison.vacancy'].sudo()
        Jail    = request.env['prison.jail'].sudo()

        created = updated = errors = 0
        error_details = []

        for i, rec in enumerate(records):
            prison_id = self._int(rec.get('prison_id'))
            if not prison_id:
                errors += 1
                error_details.append(f'Record {i}: missing or invalid prison_id.')
                continue

            jail = Jail.browse(prison_id)
            if not jail.exists():
                errors += 1
                error_details.append(f'Record {i}: prison_id {prison_id} not found.')
                continue

            vals = {
                'sanctioned_strength': self._int(rec.get('sanctioned_strength'), 0),
                'occupied_count':      self._int(rec.get('occupied_count'), 0),
                'vacancy_count':       self._int(rec.get('vacancy_count'), 0),
            }
            existing = Vacancy.search([('prison_id', '=', prison_id)], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                prison_type = rec.get('prison_type', 'sub_jail')
                valid_types = [k for k, _ in Vacancy._fields['prison_type'].selection]
                if prison_type not in valid_types:
                    prison_type = 'sub_jail'
                vals.update({
                    'prison_id':   prison_id,
                    'prison_name': rec.get('prison_name') or jail.name,
                    'prison_type': prison_type,
                })
                Vacancy.create(vals)
                created += 1

        return self._ok({
            'created':      created,
            'updated':      updated,
            'errors':       errors,
            'error_details': error_details,
            'message': f'Import complete: {created} created, {updated} updated, {errors} errors.',
        })

    # ── POST /api/vacancy/import-csv ──────────────────────────────────────────

    @http.route(
        '/api/vacancy/import-csv',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def vacancy_import_csv(self, **kwargs):
        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')

        csv_text = body.get('csv', '')
        if not csv_text:
            return self._err('"csv" field with CSV content is required.')

        clear_roles = body.get('clear_roles', False)

        try:
            from ..scripts.import_designation_vacancy import import_from_csv_string
            if clear_roles:
                _clear_canonical_roles(request.env)
            result = import_from_csv_string(request.env, csv_text)
        except Exception as e:
            _logger.exception('CSV import failed')
            return self._err(f'Import failed: {e}', status=500)

        return self._ok(result)

    # ── POST /api/vacancy/update ──────────────────────────────────────────────

    @http.route(
        '/api/vacancy/update',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def vacancy_update(self, **kwargs):
        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')

        prison_id = self._int(body.get('prison_id'))
        if not prison_id:
            return self._err('prison_id is required.')

        Vacancy = request.env['prison.vacancy'].sudo()
        vacancy = Vacancy.search([('prison_id', '=', prison_id), ('active', '=', True)], limit=1)
        if not vacancy:
            return self._err(f'No active vacancy record for prison_id {prison_id}.', status=404)

        vals = {}
        for field in ('sanctioned_strength', 'occupied_count', 'vacancy_count'):
            if field in body:
                v = self._int(body[field])
                if v is None or v < 0:
                    return self._err(f'{field} must be a non-negative integer.')
                vals[field] = v

        if not vals:
            return self._err('No updatable fields provided.')

        vacancy.write(vals)
        return self._ok({
            'prison_id':           prison_id,
            'prison_name':         vacancy.prison_name,
            'sanctioned_strength': vacancy.sanctioned_strength,
            'occupied_count':      vacancy.occupied_count,
            'vacancy_count':       vacancy.vacancy_count,
            'message': 'Vacancy record updated successfully.',
        })
