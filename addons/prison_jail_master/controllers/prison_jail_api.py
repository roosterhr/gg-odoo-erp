# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Prison Jail Hierarchy REST API  (v2 — flat 2-level model)
==========================================================

Endpoint overview
-----------------
GET  /api/jails/central                         → parent institutions (Central + SPW)
GET  /api/jails/children?parent_id=<id>         → direct children of a parent (NEW — transfer cascade)
GET  /api/jails/district?central_id=<id>        → district jails under a central (backward compat)
GET  /api/jails/sub?district_id=<id>            → sub jails under a district (backward compat shim)
GET  /api/jails/closed                          → closed sub-jails (NEW)
GET  /api/jails/<id>                            → single jail detail
GET  /api/jails/hierarchy                       → full 2-level nested structure
GET  /api/jails/hierarchy-with-vacancy          → hierarchy + vacancy merged (2-level)
GET  /api/jails/filter-list                     → grouped list for filter dropdowns
GET  /api/jails/export                          → CSV export
POST /api/jails/create                          → create facility + vacancy record

All list endpoints accept ``page`` (default 1) and ``limit`` (default 50, max 200).
All responses: { "success": true, "data": [...], "total_count": N, "page": P, "limit": L }
"""

import csv as csv_mod
import io
import json
import logging
import os

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_MAX_LIMIT = 200

# Institution types that are top-level parents
_PARENT_TYPES = ('central_jail', 'spw')

# jail_type → prison.vacancy prison_type
_JAIL_TO_VACANCY_TYPE = {
    'central_jail':     'central_prison',
    'spw':              'spw',
    'district_jail':    'district_jail',
    'sub_jail':         'sub_jail',
    'women_sub_jail':   'women_sub_jail',
    'special_sub_jail': 'special_sub_jail',
    'open_air_jail':    'open_air_jail',
    'farm_jail':        'farm_jail',
    'transit_yard':     'transit_yard',
}

_CODE_PREFIX = {
    'central_jail':     'CP',
    'spw':              'SPW',
    'district_jail':    'DJ',
    'sub_jail':         'SJ',
    'women_sub_jail':   'WSJ',
    'special_sub_jail': 'SSJ',
    'open_air_jail':    'OAJ',
    'farm_jail':        'FJ',
    'transit_yard':     'TY',
}

_TYPE_LABEL = {
    'central_jail':     'Central Prison',
    'spw':              'Special Prison for Women',
    'district_jail':    'District Jail',
    'sub_jail':         'Sub-Jail',
    'women_sub_jail':   'Women Sub-Jail',
    'special_sub_jail': 'Special Sub-Jail',
    'open_air_jail':    'Open Air Jail',
    'farm_jail':        'Farm Jail',
    'transit_yard':     'Transit Yard',
}


class PrisonJailApiController(http.Controller):

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _ok(self, data, total_count, page, limit):
        return self._json_response({
            'success': True,
            'total_count': total_count,
            'page': page,
            'limit': limit,
            'data': data,
        })

    def _err(self, message, status=400):
        return self._json_response(
            {'success': False, 'message': message}, status=status
        )

    def _parse_pagination(self, kwargs):
        try:
            page  = max(1, int(kwargs.get('page', 1)))
            limit = max(1, min(_MAX_LIMIT, int(kwargs.get('limit', 50))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Invalid pagination parameter: {exc}') from exc
        return page, limit

    def _format_jail(self, rec):
        return {
            'id':               rec.id,
            'name':             rec.name,
            'code':             rec.code or '',
            'jail_type':        rec.jail_type,
            'institution_type': rec.jail_type,
            'institution_label': _TYPE_LABEL.get(rec.jail_type, rec.jail_type),
            'hierarchy_type':   rec.hierarchy_type,
            'is_closed':        rec.is_closed,
            'closed_remarks':   rec.closed_remarks or '',
            'parent_id':        rec.parent_id.id if rec.parent_id else None,
            'parent_name':      rec.parent_id.name if rec.parent_id else '',
            'central_jail_id':  rec.central_jail_id.id if rec.central_jail_id else None,
            'central_jail_name': rec.central_jail_id.name if rec.central_jail_id else '',
            'district':         rec.district or '',
            'state':            rec.state_id.name if rec.state_id else '',
            'is_hill_station':  rec.is_hill_station,
            'external_ref':     rec.external_ref or '',
            'child_count':      rec.child_count,
        }

    def _fetch_list(self, domain, kwargs):
        page, limit = self._parse_pagination(kwargs)
        offset = (page - 1) * limit
        Jail = request.env['prison.jail'].sudo()
        total   = Jail.search_count(domain)
        records = Jail.search(domain, offset=offset, limit=limit, order='sequence, name')
        return [self._format_jail(r) for r in records], total, page, limit

    def _require_auth(self):
        uid = request.session.uid
        if not uid:
            return None, self._json_response(
                {'success': False, 'message': 'Authentication required'}, status=401
            )
        return uid, None

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

    def _vacancy_dict(self, vacancy_map, jail_id):
        v = vacancy_map.get(jail_id)
        if v:
            return {
                'sanctioned_strength': v.sanctioned_strength,
                'occupied_count':      v.occupied_count,
                'vacancy_count':       v.vacancy_count,
                'vacancy_available':   v.vacancy_count > 0,
            }
        return {
            'sanctioned_strength': 0,
            'occupied_count':      0,
            'vacancy_count':       0,
            'vacancy_available':   False,
        }

    def _designation_strength(self, desig_map, jail_id):
        """
        Aggregate strength from prison.designation.vacancy records for a jail.
        This is the single source of truth — matches Vacancy Dashboard exactly.
        Returns (sanctioned, filled, vacancy).
        """
        rows = desig_map.get(jail_id, [])
        if not rows:
            return 0, 0, 0
        sanctioned = sum(r.sanctioned_strength for r in rows)
        filled     = sum(r.filled_strength     for r in rows)
        vacancy    = sum(r.vacancy_count       for r in rows)
        return sanctioned, filled, vacancy

    def _generate_jail_code(self, jail_type):
        prefix = _CODE_PREFIX.get(jail_type, 'XX')
        Jail = request.env['prison.jail'].sudo()
        existing = Jail.search([('code', 'like', f'{prefix}%'), ('active', 'in', [True, False])])
        nums = []
        for j in existing:
            try:
                nums.append(int(j.code[len(prefix):]))
            except (ValueError, TypeError):
                pass
        return f'{prefix}{max(nums, default=0) + 1:03d}'

    # ── API 1: Central Jails / Parent Institutions ───────────────────────────

    @http.route(
        '/api/jails/central',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_central_jails(self, **kwargs):
        """
        Return all active parent institutions (Central Prisons + SPW).

        Query params:
          hierarchy_type = 'general' | 'women'  (optional filter)
          page, limit
        """
        try:
            domain = [
                ('jail_type', 'in', list(_PARENT_TYPES)),
                ('active', '=', True),
            ]
            ht = (kwargs.get('hierarchy_type') or '').strip().lower()
            if ht in ('general', 'women'):
                domain.append(('hierarchy_type', '=', ht))

            data, total, page, limit = self._fetch_list(domain, kwargs)
            return self._ok(data, total, page, limit)
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            _logger.exception('GET /api/jails/central failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 2: Children of a Parent (NEW — transfer cascade) ─────────────────

    @http.route(
        '/api/jails/children',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_children(self, **kwargs):
        """
        Return all direct children of a Central Prison or SPW.
        Replaces the 3-step cascade (central → district → sub).

        Query params:
          parent_id      (required)
          exclude_closed = 'true' | 'false'  (default: true)
          page, limit
        """
        try:
            parent_id = self._int(kwargs.get('parent_id'))
            if not parent_id:
                return self._err('Missing required query parameter: parent_id')

            parent = request.env['prison.jail'].sudo().browse(parent_id)
            if not parent.exists() or parent.jail_type not in _PARENT_TYPES:
                return self._err(
                    f'No active Central Prison or SPW found with id={parent_id}',
                    status=404,
                )

            exclude_closed = kwargs.get('exclude_closed', 'true').lower() != 'false'

            domain = [
                ('parent_id', '=', parent_id),
                ('active', '=', True),
            ]
            if exclude_closed:
                domain.append(('is_closed', '=', False))

            data, total, page, limit = self._fetch_list(domain, kwargs)
            return self._ok(data, total, page, limit)

        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            _logger.exception('GET /api/jails/children failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 3: District Jails by Central Jail (backward compat) ──────────────

    @http.route(
        '/api/jails/district',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_district_jails(self, **kwargs):
        """
        Return District Jails that belong to a given Central Jail.
        Backward-compatible — still functional after v2 migration.

        Query params: central_id (required), page, limit
        """
        try:
            central_id = self._int(kwargs.get('central_id'))
            if not central_id:
                return self._err('Missing required query parameter: central_id')

            central = request.env['prison.jail'].sudo().browse(central_id)
            if not central.exists() or central.jail_type not in _PARENT_TYPES:
                return self._err(
                    f'No active parent institution found with id={central_id}',
                    status=404,
                )

            domain = [
                ('jail_type', '=', 'district_jail'),
                ('parent_id', '=', central_id),
                ('active', '=', True),
            ]
            data, total, page, limit = self._fetch_list(domain, kwargs)
            return self._ok(data, total, page, limit)

        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            _logger.exception('GET /api/jails/district failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 4: Sub Jails (backward compat shim) ───────────────────────────────

    @http.route(
        '/api/jails/sub',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_sub_jails(self, **kwargs):
        """
        Return Sub Jails under a given institution.
        After v2 migration, district jails have no sub-jail children —
        this returns an empty list gracefully (not 404) for backward compat.

        Query params: district_id (required), page, limit
        """
        try:
            district_id = self._int(kwargs.get('district_id'))
            if not district_id:
                return self._err('Missing required query parameter: district_id')

            district = request.env['prison.jail'].sudo().browse(district_id)
            if not district.exists():
                return self._err(
                    f'Institution with id={district_id} not found', status=404
                )

            domain = [
                ('jail_type', '=', 'sub_jail'),
                ('parent_id', '=', district_id),
                ('active', '=', True),
            ]
            data, total, page, limit = self._fetch_list(domain, kwargs)
            return self._ok(data, total, page, limit)

        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            _logger.exception('GET /api/jails/sub failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 5: Closed Sub-Jails (NEW) ────────────────────────────────────────

    @http.route(
        '/api/jails/closed',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_closed_jails(self, **kwargs):
        """
        Return all operationally closed institutions.
        Closed institutions are active in DB (for employee history) but
        excluded from transfer destination selections.

        Query params: page, limit
        """
        try:
            domain = [('is_closed', '=', True), ('active', '=', True), ('jail_type', '=', 'sub_jail')]
            data, total, page, limit = self._fetch_list(domain, kwargs)
            return self._ok(data, total, page, limit)
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            _logger.exception('GET /api/jails/closed failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 6: Single Jail Detail ─────────────────────────────────────────────

    @http.route(
        '/api/jails/<int:jail_id>',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_jail_detail(self, jail_id, **_kwargs):
        try:
            jail = request.env['prison.jail'].sudo().browse(jail_id)
            if not jail.exists() or not jail.active:
                return self._err(f'Jail with id={jail_id} not found', status=404)

            payload = self._format_jail(jail)

            if jail.jail_type in _PARENT_TYPES:
                children = request.env['prison.jail'].sudo().search([
                    ('parent_id', '=', jail.id),
                    ('active', '=', True),
                ], order='sequence, name')
                payload['children'] = [
                    {
                        'id':               c.id,
                        'name':             c.name,
                        'code':             c.code or '',
                        'jail_type':        c.jail_type,
                        'institution_type': c.jail_type,
                        'hierarchy_type':   c.hierarchy_type,
                        'is_closed':        c.is_closed,
                    }
                    for c in children
                ]

            return self._json_response({'success': True, 'data': payload})

        except Exception as exc:
            _logger.exception('GET /api/jails/%s failed: %s', jail_id, exc)
            return self._err('Internal server error', status=500)

    # ── API 7: Full 2-level Hierarchy ─────────────────────────────────────────

    @http.route(
        '/api/jails/hierarchy',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_full_hierarchy(self, **_kwargs):
        """
        Full 2-level hierarchy as a nested structure.

        Response shape:
        {
            "success": true,
            "data": {
                "general": [
                    { id, name, ..., "children": [{ id, name, jail_type, ... }] }
                ],
                "women": [...]
            }
        }
        """
        try:
            Jail = request.env['prison.jail'].sudo()

            result = {'general': [], 'women': []}

            parents = Jail.search(
                [('jail_type', 'in', list(_PARENT_TYPES)), ('active', '=', True)],
                order='sequence, name',
            )
            for parent in parents:
                p_data = self._format_jail(parent)
                children = Jail.search(
                    [('parent_id', '=', parent.id), ('active', '=', True)],
                    order='sequence, name',
                )
                p_data['children'] = [self._format_jail(c) for c in children]
                # backward compat keys
                p_data['district_jails'] = [
                    self._format_jail(c) for c in children
                    if c.jail_type == 'district_jail'
                ]
                result[parent.hierarchy_type].append(p_data)

            return self._json_response({'success': True, 'data': result})

        except Exception as exc:
            _logger.exception('GET /api/jails/hierarchy failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 8: Hierarchy WITH Vacancy ────────────────────────────────────────

    @http.route(
        '/api/jails/hierarchy-with-vacancy',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_hierarchy_with_vacancy(self, **kwargs):
        """
        Full 2-level hierarchy with vacancy data merged in.
        Replaces the old 3-tier traversal.

        Query params:
          hierarchy_type = 'general' | 'women'  (optional — returns all if omitted)
          include_closed = 'true' | 'false'      (default: false)

        Response (backward-compatible with v1 consumers):
        {
          "success": true,
          "stats": { "central_prisons": N, "spw": N, "total_children": N, "total": N },
          "data": [                          ← general hierarchy parents
            {
              id, name, hierarchy_type, institution_type, ...vacancy,
              "children": [...],             ← NEW preferred key (flat)
              "district_jails": [...],       ← backward compat (district_jail children only)
              "direct_sub_jails": [...]      ← backward compat (sub_jail children only)
            }
          ],
          "women_prisons": [...]             ← SPW parents with their children
        }
        """
        try:
            Jail    = request.env['prison.jail'].sudo()
            Vacancy = request.env['prison.vacancy'].sudo()
            Desig   = request.env['prison.designation.vacancy'].sudo()

            ht_filter      = (kwargs.get('hierarchy_type') or '').strip().lower()
            include_closed = kwargs.get('include_closed', 'false').lower() == 'true'

            # prison.vacancy — aggregate per prison (primary source)
            pv_map = {}
            for v in Vacancy.search([('active', '=', True)]):
                pv_map[v.prison_id.id] = {
                    'sanctioned_strength': v.sanctioned_strength,
                    'occupied_count':      v.occupied_count,
                    'vacancy_count':       v.vacancy_count,
                }

            # prison.designation.vacancy — role-level aggregated per prison (secondary source)
            dv_map = {}
            for d in Desig.search([]):
                pid = d.prison_id.id
                if pid not in dv_map:
                    dv_map[pid] = {'sanctioned_strength': 0, 'occupied_count': 0, 'vacancy_count': 0}
                dv_map[pid]['sanctioned_strength'] += d.sanctioned_strength
                dv_map[pid]['occupied_count']      += d.filled_strength
                dv_map[pid]['vacancy_count']       += d.vacancy_count

            def _strength(jail_id):
                # Prefer designation vacancy (more granular); fall back to aggregate vacancy
                s = dv_map.get(jail_id) or pv_map.get(jail_id) or {}
                sanctioned = s.get('sanctioned_strength', 0)
                filled     = s.get('occupied_count', 0)
                vacancy    = s.get('vacancy_count', 0)
                return {
                    'sanctioned_strength': sanctioned,
                    'occupied_count':      filled,
                    'vacancy_count':       vacancy,
                    'vacancy_available':   vacancy > 0,
                }

            parent_domain = [
                ('jail_type', 'in', list(_PARENT_TYPES)),
                ('active', '=', True),
            ]
            if ht_filter in ('general', 'women'):
                parent_domain.append(('hierarchy_type', '=', ht_filter))

            all_parents = Jail.search(parent_domain, order='sequence, name')

            child_domain_base = [
                ('active', '=', True),
            ]
            if not include_closed:
                child_domain_base.append(('is_closed', '=', False))

            general_data    = []
            women_data      = []
            district_parents = []

            # Track district_jails that have active children — they become
            # their own parent sections in the dropdown.
            district_jails_with_children = set()

            def _build_parent(parent):
                p_data = self._format_jail(parent)
                # Exclude district_jails that are shown as separate parents
                # from the central prison's flat children list.
                child_domain = [('parent_id', '=', parent.id)] + child_domain_base
                children = Jail.search(child_domain, order='sequence, name')
                children_formatted = []
                for c in children:
                    if c.id in district_jails_with_children:
                        continue  # shown as its own parent section
                    c_data = self._format_jail(c)
                    c_data.update(_strength(c.id))
                    children_formatted.append(c_data)

                if children_formatted:
                    agg_s = sum(c['sanctioned_strength'] for c in children_formatted)
                    agg_f = sum(c['occupied_count']      for c in children_formatted)
                    agg_v = sum(c['vacancy_count']       for c in children_formatted)
                    p_data.update({
                        'sanctioned_strength': agg_s,
                        'occupied_count':      agg_f,
                        'vacancy_count':       agg_v,
                        'vacancy_available':   agg_v > 0,
                    })
                else:
                    p_data.update(_strength(parent.id))

                p_data['children'] = children_formatted
                p_data['district_jails'] = [
                    c for c in children_formatted if c['jail_type'] == 'district_jail'
                ]
                p_data['direct_sub_jails'] = [
                    c for c in children_formatted if c['jail_type'] == 'sub_jail'
                ]
                return p_data

            # Pass 1: find district_jails with active children → promote to parents
            all_dj = Jail.search(
                [('jail_type', '=', 'district_jail'), ('active', '=', True)],
                order='sequence, name',
            )
            for dj in all_dj:
                child_count = Jail.search_count(
                    [('parent_id', '=', dj.id)] + child_domain_base)
                if child_count > 0:
                    district_jails_with_children.add(dj.id)

            # Pass 2: build district parent entries with their own children
            for dj in all_dj:
                if dj.id not in district_jails_with_children:
                    continue
                dj_data = self._format_jail(dj)
                dj_children = Jail.search(
                    [('parent_id', '=', dj.id)] + child_domain_base,
                    order='sequence, name',
                )
                dj_children_fmt = []
                for c in dj_children:
                    c_data = self._format_jail(c)
                    c_data.update(_strength(c.id))
                    dj_children_fmt.append(c_data)

                if dj_children_fmt:
                    dj_data.update({
                        'sanctioned_strength': sum(c['sanctioned_strength'] for c in dj_children_fmt),
                        'occupied_count':      sum(c['occupied_count']      for c in dj_children_fmt),
                        'vacancy_count':       sum(c['vacancy_count']       for c in dj_children_fmt),
                        'vacancy_available':   sum(c['vacancy_count']       for c in dj_children_fmt) > 0,
                    })
                else:
                    dj_data.update(_strength(dj.id))
                dj_data['children'] = dj_children_fmt
                district_parents.append(dj_data)

            # Pass 3: build central / SPW parents
            for parent in all_parents:
                p_data = _build_parent(parent)
                if parent.hierarchy_type == 'women':
                    women_data.append(p_data)
                else:
                    general_data.append(p_data)

            total_children = Jail.search_count(
                [('jail_type', 'not in', list(_PARENT_TYPES)), ('active', '=', True), ('is_closed', '=', False)]
            )
            closed_subjails = Jail.search_count(
                [('jail_type', '=', 'sub_jail'), ('is_closed', '=', True), ('active', '=', True)]
            )

            return self._json_response({
                'success': True,
                'stats': {
                    'central_prisons':  sum(1 for p in all_parents if p.jail_type == 'central_jail'),
                    'spw':              sum(1 for p in all_parents if p.jail_type == 'spw'),
                    'district_parents': len(district_parents),
                    'total_children':   total_children,
                    'total':            len(all_parents) + total_children,
                    'closed_subjails':  closed_subjails,
                },
                'data':            general_data,
                'district_parents': district_parents,
                'women_prisons':   women_data,
                'special_women_prisons': women_data,
            })

        except Exception as exc:
            _logger.exception('GET /api/jails/hierarchy-with-vacancy failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 9: Filter List (for Personnel/Transfer filter dropdowns) ──────────

    @http.route(
        '/api/jails/filter-list',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def get_filter_list(self, **_kwargs):
        """
        Return all active jails grouped by category for filter dropdowns.

        Groups:
          1. Women Hierarchy (hierarchy_type='women')
          2. Central Prisons (jail_type='central_jail')
          3. District Jails
          4. Sub-Jails
          5. Other Institutions (open_air, farm, transit)
          6. Closed
        """
        try:
            Jail = request.env['prison.jail'].sudo()
            all_jails = Jail.search([('active', '=', True)], order='sequence, name')

            # Officers posted per prison, counted at every tier the prison
            # appears in — mirrors how the /api/employees jail_id filter matches.
            emp_counts = {}
            try:
                request.env.cr.execute("""
                    SELECT pid, SUM(cnt) FROM (
                        SELECT x_central_jail_id AS pid, COUNT(*) AS cnt
                          FROM hr_employee
                         WHERE active AND x_employee_code IS NOT NULL AND x_employee_code != ''
                           AND x_central_jail_id IS NOT NULL
                         GROUP BY 1
                        UNION ALL
                        SELECT x_district_jail_id, COUNT(*)
                          FROM hr_employee
                         WHERE active AND x_employee_code IS NOT NULL AND x_employee_code != ''
                           AND x_district_jail_id IS NOT NULL
                         GROUP BY 1
                        UNION ALL
                        SELECT x_sub_jail_id, COUNT(*)
                          FROM hr_employee
                         WHERE active AND x_employee_code IS NOT NULL AND x_employee_code != ''
                           AND x_sub_jail_id IS NOT NULL
                         GROUP BY 1
                    ) t GROUP BY pid
                """)
                emp_counts = {row[0]: int(row[1]) for row in request.env.cr.fetchall()}
            except Exception:
                _logger.exception('filter-list: employee count query failed')

            groups_map = {
                'women':   {'label': "Women's Institutions",  'jails': []},
                'central': {'label': 'Central Prisons',        'jails': []},
                'district':{'label': 'District Jails',         'jails': []},
                'sub':     {'label': 'Sub-Jails',              'jails': []},
                'other':   {'label': 'Other Institutions',     'jails': []},
                'closed':  {'label': 'Closed',                 'jails': []},
            }

            for j in all_jails:
                item = {
                    'id':               j.id,
                    'name':             j.name,
                    'jail_type':        j.jail_type,
                    'institution_type': j.jail_type,
                    'hierarchy_type':   j.hierarchy_type,
                    'is_closed':        j.is_closed,
                    'parent_id':        j.parent_id.id if j.parent_id else None,
                    'parent_name':      j.parent_id.name if j.parent_id else '',
                    'employee_count':   emp_counts.get(j.id, 0),
                }
                if j.is_closed:
                    groups_map['closed']['jails'].append(item)
                elif j.hierarchy_type == 'women':
                    groups_map['women']['jails'].append(item)
                elif j.jail_type == 'central_jail':
                    groups_map['central']['jails'].append(item)
                elif j.jail_type == 'district_jail':
                    groups_map['district']['jails'].append(item)
                elif j.jail_type == 'sub_jail':
                    groups_map['sub']['jails'].append(item)
                else:
                    groups_map['other']['jails'].append(item)

            groups = [g for g in groups_map.values() if g['jails']]
            return self._json_response({'success': True, 'groups': groups})

        except Exception as exc:
            _logger.exception('GET /api/jails/filter-list failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── API 10: Create Facility ───────────────────────────────────────────────

    @http.route(
        '/api/jails/create',
        auth='none', type='http', methods=['POST'], csrf=False,
    )
    def create_facility(self, **_kwargs):
        """
        Create a new prison/jail facility.

        Request body:
        {
          "jail_type":         "central_jail" | "spw" | "district_jail" | "sub_jail" | ...,
          "hierarchy_type":    "general" | "women"   (optional — auto-set from jail_type if omitted),
          "name":              "...",
          "parent_id":         <int> | null,
          "district":          "...",
          "sanctioned_strength": <int>,
          "occupied_count":    <int>,
          "sequence":          <int>
        }
        """
        uid, err = self._require_auth()
        if err:
            return err

        body = self._parse_body()
        if body is None:
            return self._err('Invalid JSON body.')

        jail_type = (body.get('jail_type') or '').strip()
        valid_types = list(_JAIL_TO_VACANCY_TYPE.keys())
        if jail_type not in valid_types:
            return self._err(
                f'jail_type must be one of: {", ".join(valid_types)}'
            )

        name = (body.get('name') or '').strip()
        if not name:
            return self._err('name is required.')

        # Determine hierarchy_type
        hierarchy_type = (body.get('hierarchy_type') or '').strip().lower()
        if hierarchy_type not in ('general', 'women'):
            # auto-derive
            if jail_type in ('spw', 'women_sub_jail'):
                hierarchy_type = 'women'
            else:
                hierarchy_type = 'general'

        parent_id = self._int(body.get('parent_id'))
        sanctioned = max(0, self._int(body.get('sanctioned_strength'), 0))
        occupied   = max(0, self._int(body.get('occupied_count'), 0))
        if occupied > sanctioned:
            return self._err('Occupied count cannot exceed sanctioned strength.')

        Jail    = request.env['prison.jail'].sudo()
        Vacancy = request.env['prison.vacancy'].sudo()

        # Validate parent rules
        if jail_type in _PARENT_TYPES:
            if parent_id:
                return self._err(
                    f'{_TYPE_LABEL.get(jail_type, jail_type)} cannot have a parent.'
                )
        else:
            if not parent_id:
                return self._err(
                    f'{_TYPE_LABEL.get(jail_type, jail_type)} requires a parent '
                    '(Central Prison or Special Prison for Women).'
                )
            parent = Jail.browse(parent_id)
            if not parent.exists() or parent.jail_type not in _PARENT_TYPES:
                return self._err(
                    'Parent must be a Central Prison or Special Prison for Women.'
                )

        # Duplicate name+hierarchy check
        existing = Jail.search([
            ('name', '=ilike', name),
            ('hierarchy_type', '=', hierarchy_type),
        ], limit=1)
        if existing:
            return self._err(
                f'An institution named "{name}" already exists in the '
                f'{hierarchy_type.title()} hierarchy.'
            )

        code = self._generate_jail_code(jail_type)

        vals = {
            'name':           name,
            'jail_type':      jail_type,
            'hierarchy_type': hierarchy_type,
            'code':           code,
            'district':       (body.get('district') or '').strip() or False,
            'sequence':       self._int(body.get('sequence'), 10),
            'active':         True,
        }
        if parent_id:
            vals['parent_id'] = parent_id

        new_jail = Jail.create(vals)

        vacancy_type = _JAIL_TO_VACANCY_TYPE[jail_type]
        vacancy = Vacancy.create({
            'prison_id':           new_jail.id,
            'prison_name':         new_jail.name,
            'prison_type':         vacancy_type,
            'sanctioned_strength': sanctioned,
            'occupied_count':      occupied,
            'vacancy_count':       max(0, sanctioned - occupied),
        })

        return self._json_response({
            'success': True,
            'message': f'Facility "{name}" created successfully.',
            'data': {
                **self._format_jail(new_jail),
                'sanctioned_strength': vacancy.sanctioned_strength,
                'occupied_count':      vacancy.occupied_count,
                'vacancy_count':       vacancy.vacancy_count,
            },
        }, status=201)

    # ── API 11: Export Hierarchy ──────────────────────────────────────────────

    @http.route(
        '/api/jails/export',
        auth='none', type='http', methods=['GET'], csrf=False,
    )
    def export_hierarchy(self, **kwargs):
        """Export the full prison hierarchy as CSV."""
        uid, err = self._require_auth()
        if err:
            return err

        try:
            Jail    = request.env['prison.jail'].sudo()
            Vacancy = request.env['prison.vacancy'].sudo()

            vacancy_map = {v.prison_id.id: v for v in Vacancy.search([('active', '=', True)])}

            domain = [('active', '=', True)]
            q  = (kwargs.get('q') or '').strip()
            jt = (kwargs.get('jail_type') or '').strip()
            ht = (kwargs.get('hierarchy_type') or '').strip().lower()

            if q:
                domain += ['|', ('name', 'ilike', q), ('code', 'ilike', q)]
            if jt:
                domain.append(('jail_type', '=', jt))
            if ht in ('general', 'women'):
                domain.append(('hierarchy_type', '=', ht))

            jails = Jail.search(domain, order='hierarchy_type, jail_type, sequence, name')

            output = io.StringIO()
            writer = csv_mod.writer(output)
            writer.writerow([
                'Facility Code', 'Facility Name', 'Institution Type', 'Institution Type Label',
                'Hierarchy', 'Parent Facility', 'District',
                'Sanctioned Strength', 'Filled Strength', 'Vacancy Count',
                'Closed', 'Closed Remarks', 'Status',
            ])

            for j in jails:
                v = vacancy_map.get(j.id)
                writer.writerow([
                    j.code or '',
                    j.name,
                    j.jail_type,
                    _TYPE_LABEL.get(j.jail_type, j.jail_type),
                    j.hierarchy_type.title() if j.hierarchy_type else '',
                    j.parent_id.name if j.parent_id else '',
                    j.district or '',
                    v.sanctioned_strength if v else 0,
                    v.occupied_count      if v else 0,
                    v.vacancy_count       if v else 0,
                    'Yes' if j.is_closed else 'No',
                    j.closed_remarks or '',
                    'Active' if j.active else 'Inactive',
                ])

            csv_bytes = output.getvalue().encode('utf-8-sig')
            return request.make_response(
                csv_bytes,
                headers=[
                    ('Content-Type', 'text/csv; charset=utf-8'),
                    ('Content-Disposition', 'attachment; filename="prisons_hierarchy_v2.csv"'),
                ],
            )

        except Exception as exc:
            _logger.exception('GET /api/jails/export failed: %s', exc)

    # ── Seed endpoint (one-time data import) ──────────────────────────────────

    @http.route('/api/admin/seed-spw', methods=['POST'], auth='none', type='json', csrf=False)
    def seed_spw_data(self, **kwargs):
        """
        POST /api/admin/seed-spw
        Body: { "secret": "<SEED_SECRET>" }

        One-time seed: creates missing SPW + women sub-jails + closed sub-jails.
        Safe to call multiple times — skips records that already exist.
        Remove this endpoint after DEV/PROD are in sync.
        """
        SEED_SECRET = 'tnpd-seed-2025'
        body = request.get_json_data() or {}
        if body.get('secret') != SEED_SECRET:
            return {'success': False, 'error': 'Unauthorized'}

        Jail = request.env['prison.jail'].sudo()
        created, skipped = [], []

        def find(name, jail_type=None):
            d = [('name', '=', name)]
            if jail_type:
                d.append(('jail_type', '=', jail_type))
            return Jail.with_context(active_test=False).search(d, limit=1)

        reactivated = []

        def upsert(name, jail_type, vals):
            rec = find(name, jail_type)
            if rec:
                # Always reactivate women records and patch hierarchy_type
                update = {}
                if not rec.active and vals.get('hierarchy_type') == 'women':
                    update['active'] = True
                if rec.hierarchy_type != vals.get('hierarchy_type', rec.hierarchy_type):
                    update['hierarchy_type'] = vals['hierarchy_type']
                if update:
                    rec.write(update)
                    reactivated.append(f'{name} [{jail_type}] {update}')
                else:
                    skipped.append(f'{name} [{jail_type}]')
                return rec
            rec = Jail.with_context(active_test=False).create(dict(vals, name=name, jail_type=jail_type))
            created.append(f'{name} [{jail_type}] id={rec.id}')
            return rec

        def get_parent_id(name, jail_type, fallback_name=None, fallback_type='central_jail'):
            rec = find(name, jail_type)
            if rec:
                return rec.id
            if fallback_name:
                rec = find(fallback_name, fallback_type)
                if rec:
                    return rec.id
            return None

        try:
            # 1. SPW — top-level
            spw_chennai    = upsert('Chennai',         'spw', {'hierarchy_type': 'women', 'sequence': 15})
            spw_vellore    = upsert('Vellore',         'spw', {'hierarchy_type': 'women', 'sequence': 25})
            spw_coimbatore = upsert('Coimbatore',      'spw', {'hierarchy_type': 'women', 'sequence': 65})
            spw_trichy     = upsert('Tiruchirappalli', 'spw', {'hierarchy_type': 'women', 'sequence': 45})
            spw_madurai    = upsert('Madurai',         'spw', {'hierarchy_type': 'women', 'sequence': 75})

            # 2. Women sub-jails
            upsert('Cuddalore',  'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_vellore.id,    'is_closed': True,  'sequence': 40})
            upsert('Dharmapuri', 'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_coimbatore.id, 'sequence': 10})
            upsert('Nilakottai', 'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_madurai.id,    'sequence': 30})
            upsert('Paramakudi', 'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_madurai.id,    'sequence': 20})
            upsert('Thiruvarur', 'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_trichy.id,     'sequence': 40})
            upsert('Thuckalay',  'women_sub_jail', {'hierarchy_type': 'women', 'parent_id': spw_madurai.id,    'sequence': 20})

            # 3. Special sub-jails
            upsert('Kokkirakulam (Women)', 'special_sub_jail', {'hierarchy_type': 'women',   'parent_id': spw_madurai.id,    'sequence': 30})
            upsert('Salem (Women)',        'special_sub_jail', {'hierarchy_type': 'women',   'parent_id': spw_coimbatore.id, 'sequence': 30})
            pid = get_parent_id('Palayamkottai', 'central_jail')
            if pid:
                upsert('Nanguneri (Men)', 'special_sub_jail', {'hierarchy_type': 'general', 'parent_id': pid, 'sequence': 20})

            # 4. Transit yard
            pid = get_parent_id('Chennai - II', 'central_jail')
            if pid:
                upsert('Puzhal (Young Offenders Correctional Facility)', 'transit_yard', {'hierarchy_type': 'general', 'parent_id': pid, 'sequence': 20})

            # 5. Closed sub-jails
            closed = [
                ('S.J. Maduranthagam (Closed)', 'sub_jail',     'Chengalpattu',        'district_jail', 'Chennai - I',     20),
                ('S.J. Arani (Closed)',         'sub_jail',     'Tirupattur District', 'district_jail', 'Vellore',         30),
                ('S.J. Cheyyar (Closed)',       'sub_jail',     'Tirupattur District', 'district_jail', 'Vellore',         40),
                ('S.J. Cuddalore (Closed)',     'sub_jail',     'Cuddalore District',  'district_jail', 'Cuddalore',       50),
                ('S.J. Parangipet (Closed)',    'sub_jail',     'Cuddalore District',  'district_jail', 'Cuddalore',       60),
                ('S.J. Keeranur (Closed)',      'sub_jail',     'Pudukkottai',         'district_jail', 'Tiruchirappalli', 20),
                ('S.J. Pattukottai (Closed)',   'sub_jail',     'Thanjavur District',  'district_jail', 'Tiruchirappalli', 60),
                ('S.J. Manapparai (Closed)',    'sub_jail',     'Trichy District',     'district_jail', 'Tiruchirappalli', 30),
                ('S.J. Musiri (Closed)',        'sub_jail',     'Trichy District',     'district_jail', 'Tiruchirappalli', 40),
                ('S.J. Rasipuram (Closed)',     'sub_jail',     'Namakkal District',   'district_jail', 'Salem',           30),
                ('S.J. Paramathivelur (Closed)','sub_jail',     'Namakkal District',   'district_jail', 'Salem',           40),
                ('S.J. Mettupalayam (Closed)',  'sub_jail',     'Coimbatore District', 'district_jail', 'Coimbatore',      30),
                ('S.J. Thiruvadanai (Closed)',  'sub_jail',     'Ramanathapuram',      'district_jail', 'Madurai',         40),
                ('S.J. Thiruchendur (Closed)',  'sub_jail',     'Thoothukudi',         'district_jail', 'Palayamkottai',   40),
                ('S.J. Kodaikanal (Closed)',    'sub_jail',     'Dindigul',            'district_jail', 'Madurai',         40),
                ('D.J. Attur (Closed)',         'district_jail','Salem',               'central_jail',  None,              60),
            ]
            for name, jtype, pname, ptype, fallback, seq in closed:
                pid = get_parent_id(pname, ptype, fallback, 'central_jail')
                if pid:
                    upsert(name, jtype, {'parent_id': pid, 'active': False, 'sequence': seq})
                else:
                    skipped.append(f'{name} [no parent found]')

            request.env.cr.commit()

            return {
                'success': True,
                'created_count':    len(created),
                'reactivated_count': len(reactivated),
                'skipped_count':    len(skipped),
                'created':     created,
                'reactivated': reactivated,
                'skipped':     skipped,
                'stats': {
                    'spw':            Jail.search_count([('jail_type', '=', 'spw')]),
                    'women_sub_jail': Jail.search_count([('jail_type', '=', 'women_sub_jail')]),
                    'closed':         Jail.with_context(active_test=False).search_count([('active', '=', False)]),
                    'total':          Jail.with_context(active_test=False).search_count([]),
                },
            }

        except Exception as exc:
            _logger.exception('POST /api/admin/seed-spw failed: %s', exc)
            return {'success': False, 'error': str(exc)}

    # ── Full hierarchy sync endpoint ───────────────────────────────────────────

    @http.route('/api/admin/sync-hierarchy', methods=['POST'], auth='none', type='json', csrf=False)
    def sync_hierarchy(self, **kwargs):
        """
        POST /api/admin/sync-hierarchy
        Body: { "secret": "<SYNC_SECRET>" }

        Syncs DEV prison hierarchy to match LOCAL canonical state:
          - Renames misnamed records (S.J. prefix cleanup)
          - Migrates SPW-related sub_jails to women hierarchy
          - Deactivates stale placeholder records
          - Creates missing active jails
          - Creates missing closed jails
        Safe to call multiple times. Remove after DEV/PROD sync confirmed.
        """
        SYNC_SECRET = 'tnpd-sync-2025'
        body = request.get_json_data() or {}
        if body.get('secret') != SYNC_SECRET:
            return {'success': False, 'error': 'Unauthorized'}

        Jail = request.env['prison.jail'].sudo()
        log = {'renamed': [], 'migrated': [], 'deactivated': [], 'created': [], 'skipped': [], 'errors': []}

        def find(name, jail_type=None, parent_name=None):
            d = [('name', '=', name)]
            if jail_type:    d.append(('jail_type', '=', jail_type))
            if parent_name:
                parent = Jail.with_context(active_test=False).search([('name', '=', parent_name)], limit=1)
                if parent: d.append(('parent_id', '=', parent.id))
            return Jail.with_context(active_test=False).search(d, limit=1)

        def find_parent(name, jail_type=None):
            d = [('name', '=', name)]
            if jail_type: d.append(('jail_type', '=', jail_type))
            return Jail.with_context(active_test=False).search(d, limit=1)

        try:
            # ── 1. RENAMES ────────────────────────────────────────────────────
            renames = [
                # (old_name, old_jail_type, old_parent, new_name, new_jail_type)
                ('S.J. Kanchipuram',   'sub_jail', 'Chennai - I',    'Kancheepuram',    'sub_jail'),
                ('S.J. Thiruvallur',   'sub_jail', 'Chennai - I',    'Tiruvallur',       'sub_jail'),
                ('S.J. Tirupattur',    'sub_jail', 'Vellore',        'Tirupathur',       'sub_jail'),
                ('S.J. Walaja',        'sub_jail', 'Vellore',        'Walajah',          'sub_jail'),
                ('S.J. Thirukovilur',  'sub_jail', 'Cuddalore',      'Thirukovilur',     'sub_jail'),
                ('S.J. Thiruchengodu', 'sub_jail', 'Salem',          'Thiruchengodu',    'sub_jail'),
                ('OAJ, Singanallur',   'sub_jail', 'Coimbatore',     'Singanallur',      'open_air_jail'),
                ('S.J. Udumalaipet',   'sub_jail', 'Coimbatore',     'Udumalaipettai',   'sub_jail'),
                ('S.J. Tiruppattur',   'sub_jail', 'Madurai',        'Tiruppathur',      'sub_jail'),
                ('S.J. Sankarankoil',  'sub_jail', 'Palayamkottai',  'Sankarankoil',     'sub_jail'),
            ]
            for old_name, old_type, parent_name, new_name, new_type in renames:
                rec = find(old_name, old_type, parent_name)
                if rec:
                    vals = {}
                    if rec.name != new_name:      vals['name']      = new_name
                    if rec.jail_type != new_type: vals['jail_type'] = new_type
                    if vals:
                        rec.write(vals)
                        log['renamed'].append(f'{old_name} → {new_name}')
                    else:
                        log['skipped'].append(f'rename: {old_name} (already correct)')
                else:
                    # might already be renamed — skip silently if target exists
                    if not find(new_name, new_type, parent_name):
                        log['errors'].append(f'rename: source not found: {old_name}')

            # ── 2. MIGRATE to SPW hierarchy ───────────────────────────────────
            spw_madurai    = find('Madurai',         'spw')
            spw_coimbatore = find('Coimbatore',      'spw')

            migrations = [
                # (old_name, old_type, old_parent, new_name, new_type, new_hierarchy, spw_rec)
                ('S.J. Nilakottai',  'sub_jail', 'Madurai',       'Nilakottai',          'women_sub_jail',   'women',   spw_madurai),
                ('S.J. Paramakudi',  'sub_jail', 'Madurai',       'Paramakudi',          'women_sub_jail',   'women',   spw_madurai),
                ('S.J. Thuckalay',   'sub_jail', 'Palayamkottai', 'Thuckalay',           'women_sub_jail',   'women',   spw_madurai),
                ('S.J. Kokkirakulam','sub_jail', 'Palayamkottai', 'Kokkirakulam (Women)','special_sub_jail', 'women',   spw_madurai),
            ]
            for old_name, old_type, old_parent, new_name, new_type, new_hier, spw_rec in migrations:
                rec = find(old_name, old_type, old_parent)
                if rec and spw_rec:
                    rec.write({
                        'name':           new_name,
                        'jail_type':      new_type,
                        'hierarchy_type': new_hier,
                        'parent_id':      spw_rec.id,
                    })
                    log['migrated'].append(f'{old_name} → {new_name} (SPW {spw_rec.name})')
                elif not rec:
                    if not find(new_name, new_type):
                        log['errors'].append(f'migrate: source not found: {old_name}')

            # S.J. Manapparai: deactivate (it becomes closed Manaparai)
            sj_manapparai = find('S.J. Manapparai', 'sub_jail', 'Tiruchirappalli')
            if sj_manapparai and sj_manapparai.active:
                sj_manapparai.write({'active': False})
                log['deactivated'].append('S.J. Manapparai (→ closed)')

            # ── 3. DEACTIVATE stale placeholders ──────────────────────────────
            to_deactivate = [
                ('SPW, Vellore',    'sub_jail', 'Vellore'),
                ('SPW, Trichy',     'sub_jail', 'Tiruchirappalli'),
                ('SPW, Salem',      'sub_jail', 'Salem'),
                ('SPW, Coimbatore', 'sub_jail', 'Coimbatore'),
                ('S.J. Madurai',    'sub_jail', 'Madurai'),
            ]
            for name, jtype, parent_name in to_deactivate:
                rec = find(name, jtype, parent_name)
                if rec and rec.active:
                    rec.write({'active': False})
                    log['deactivated'].append(name)

            # ── 4. CREATE missing active jails ────────────────────────────────
            def upsert(name, jail_type, vals):
                # Search by name+jail_type only — parent may differ across envs
                existing = Jail.with_context(active_test=False).search(
                    [('name', '=', name), ('jail_type', '=', jail_type)], limit=1)
                if existing:
                    log['skipped'].append(f'create: {name} [{jail_type}]')
                    return existing
                clean_vals = {k: v for k, v in vals.items() if not k.startswith('_')}
                rec = Jail.with_context(active_test=False).create(
                    dict(clean_vals, name=name, jail_type=jail_type)
                )
                log['created'].append(f'{name} [{jail_type}]')
                return rec

            vellore  = find_parent('Vellore',      'central_jail')
            salem    = find_parent('Salem',         'central_jail')
            madurai  = find_parent('Madurai',       'central_jail')
            palk     = find_parent('Palayamkottai', 'central_jail')
            trichy   = find_parent('Tiruchirappalli','central_jail')
            cuddalore = find_parent('Cuddalore',    'central_jail')
            chennai1  = find_parent('Chennai - I',  'central_jail')

            if vellore:
                upsert('Cheyyar', 'sub_jail', {'hierarchy_type': 'general', 'parent_id': vellore.id, '_parent_name': 'Vellore', 'sequence': 35})
            if salem:
                upsert('Salem', 'farm_jail', {'hierarchy_type': 'general', 'parent_id': salem.id, '_parent_name': 'Salem', 'sequence': 95})
            if madurai:
                upsert('Kodaikanal', 'sub_jail', {'hierarchy_type': 'general', 'parent_id': madurai.id, '_parent_name': 'Madurai', 'sequence': 95})
                # Fix Purasaraidaiudaippu: sub_jail → open_air_jail
                purasa = find('Purasaraidaiudaippu', 'sub_jail', 'Madurai')
                if purasa:
                    purasa.write({'jail_type': 'open_air_jail'})
                    log['migrated'].append('Purasaraidaiudaippu: sub_jail → open_air_jail')
            if palk:
                upsert('Nanguneri (Men)', 'special_sub_jail', {'hierarchy_type': 'general', 'parent_id': palk.id, '_parent_name': 'Palayamkottai', 'sequence': 20})

            # Pudukkottai cluster — all under Tiruchirappalli central prison (flat v2 model)
            if trichy:
                pudukkottai_subs = [
                    ('Aranthangi',        'sub_jail',      40),
                    ('Kumbakonam',        'sub_jail',      50),
                    ('Mannargudi',        'sub_jail',      55),
                    ('Mayiladuthurai',    'sub_jail',      56),
                    ('Nagapattinam',      'district_jail', 57),
                    ('Nannilam',          'sub_jail',      58),
                    ('Papanasam',         'sub_jail',      59),
                    ('Sirkali',           'sub_jail',      60),
                    ('Thanjavur',         'sub_jail',      65),
                    ('Thiruthuraipoondi', 'sub_jail',      70),
                ]
                for pname, pjtype, pseq in pudukkottai_subs:
                    upsert(pname, pjtype, {
                        'hierarchy_type': 'general',
                        'parent_id': trichy.id,
                        'sequence': pseq,
                    })

            # SPW: Tiruppur (Annex) under Coimbatore SPW
            spw_coimbatore = find_parent('Coimbatore', 'spw')
            if spw_coimbatore:
                upsert('Tiruppur (Annex)', 'women_sub_jail', {
                    'hierarchy_type': 'women', 'parent_id': spw_coimbatore.id, 'sequence': 20})

            # ── 5. CREATE missing closed jails ────────────────────────────────
            closed_to_add = [
                # (name, jail_type, parent_name, parent_type, fallback_central)
                ('Madurantagam',              'sub_jail', 'Chennai - I',    'central_jail', None),
                ('Pattukottai',               'sub_jail', 'Chennai - I',    'central_jail', None),
                ('Arani',                     'sub_jail', 'Vellore',        'central_jail', None),
                ('Sattur',                    'sub_jail', 'Palayamkottai',  'central_jail', None),
                ('Keeranur',                  'sub_jail', 'Tiruchirappalli','central_jail', None),
                ('Manaparai',                 'sub_jail', 'Tiruchirappalli','central_jail', None),
                ('Rasipuram',                 'sub_jail', 'Salem',          'central_jail', None),
                ('Paramathivelur',            'sub_jail', 'Salem',          'central_jail', None),
                ('Thiruvadanai',              'sub_jail', 'Madurai',        'central_jail', None),
                ('Thiruchendur',              'sub_jail', 'Palayamkottai',  'central_jail', None),
                ('Portonovo @ Parangipettai', 'sub_jail', 'Cuddalore',      'central_jail', None),
                ('Cuddalore',                 'sub_jail', 'Cuddalore',      'central_jail', None),
                ('Mettupalayam',              'sub_jail', 'Coimbatore',     'central_jail', None),
            ]
            for name, jtype, parent_name, parent_type, _ in closed_to_add:
                parent = find_parent(parent_name, parent_type)
                if parent:
                    existing = Jail.with_context(active_test=False).search(
                        [('name', '=', name), ('jail_type', '=', jtype),
                         ('parent_id', '=', parent.id), ('active', '=', False)], limit=1
                    )
                    if existing:
                        log['skipped'].append(f'closed: {name}')
                    else:
                        Jail.with_context(active_test=False).create({
                            'name': name, 'jail_type': jtype,
                            'parent_id': parent.id, 'active': False,
                        })
                        log['created'].append(f'{name} [closed]')

            # ── 6. REACTIVATE SPW + women hierarchy records ───────────────
            # Only reactivate if no active record with same (name, hierarchy_type) exists
            women_types = ('spw', 'women_sub_jail', 'special_sub_jail')
            inactive_women = Jail.with_context(active_test=False).search([
                ('jail_type', 'in', women_types),
                ('hierarchy_type', '=', 'women'),
                ('active', '=', False),
            ])
            for r in inactive_women:
                has_dup = Jail.search([
                    ('name', '=', r.name),
                    ('hierarchy_type', '=', r.hierarchy_type),
                    ('id', '!=', r.id),
                ], limit=1)
                if has_dup:
                    log['skipped'].append(f'dup-inactive: {r.name} [{r.jail_type}]')
                else:
                    r.write({'active': True})
                    log['migrated'].append(f'reactivated: {r.name} [{r.jail_type}]')

            # Reactivate transit_yard / open_air_jail / farm_jail if no active dup
            inactive_special = Jail.with_context(active_test=False).search([
                ('jail_type', 'in', ('transit_yard', 'open_air_jail', 'farm_jail')),
                ('active', '=', False),
            ])
            for r in inactive_special:
                has_dup = Jail.search([
                    ('name', '=', r.name),
                    ('hierarchy_type', '=', r.hierarchy_type),
                    ('id', '!=', r.id),
                ], limit=1)
                if not has_dup:
                    r.write({'active': True})
                    log['migrated'].append(f'reactivated: {r.name} [{r.jail_type}]')

            # Reactivate Nanguneri (Men) special_sub_jail (general hierarchy — safe)
            nanguneri = Jail.with_context(active_test=False).search([
                ('name', '=', 'Nanguneri (Men)'), ('jail_type', '=', 'special_sub_jail'),
            ], limit=1)
            if nanguneri and not nanguneri.active:
                nanguneri.write({'active': True})
                log['migrated'].append('reactivated: Nanguneri (Men) [special_sub_jail]')

            # ── 7. FIX women sub-jail parent → correct SPW ───────────────────
            # Map: (child_name, child_jail_type) → SPW name + activate + extra vals
            spw_parent_map = {
                'Cuddalore':            ('Vellore',         'women_sub_jail', {'is_closed': True}),
                'Villupuram':           ('Vellore',         'women_sub_jail', {}),
                'Dharmapuri':           ('Coimbatore',      'women_sub_jail', {}),
                'Tiruppur (Annex)':     ('Coimbatore',      'women_sub_jail', {}),
                'Salem (Women)':        ('Coimbatore',      'special_sub_jail', {}),
                'Thiruvarur':           ('Tiruchirappalli', 'women_sub_jail', {}),
                'Nilakottai':           ('Madurai',         'women_sub_jail', {}),
                'Paramakudi':           ('Madurai',         'women_sub_jail', {}),
                'Thuckalay':            ('Madurai',         'women_sub_jail', {}),
                'Kokkirakulam (Women)': ('Madurai',         'special_sub_jail', {}),
            }
            for child_name, (spw_name, child_type, extra_vals) in spw_parent_map.items():
                spw_rec = Jail.with_context(active_test=False).search(
                    [('name', '=', spw_name), ('jail_type', '=', 'spw')], limit=1)
                if not spw_rec:
                    continue
                child = Jail.with_context(active_test=False).search(
                    [('name', '=', child_name), ('jail_type', '=', child_type)], limit=1)
                if not child:
                    continue
                updates = dict(extra_vals)
                if child.parent_id.id != spw_rec.id:
                    updates['parent_id'] = spw_rec.id
                if not child.active and child_name != 'Cuddalore':
                    updates['active'] = True
                if updates:
                    child.write(updates)
                    log['migrated'].append(
                        f'reparented/fixed: {child_name} → {spw_name} SPW {updates}')

            # ── 8. DEDUP: remove extra duplicate transit/women records ────────
            # Correct Puzhal is under Chennai-I; deactivate the duplicate under Chennai-II
            chennai1 = find_parent('Chennai - I', 'central_jail')
            chennai2 = find_parent('Chennai - II', 'central_jail')
            if chennai1 and chennai2:
                # Ensure the Chennai-I Puzhal is active
                puzhal_c1 = Jail.with_context(active_test=False).search([
                    ('name', 'ilike', 'Puzhal'),
                    ('jail_type', '=', 'transit_yard'),
                    ('parent_id', '=', chennai1.id),
                ], limit=1)
                if puzhal_c1 and not puzhal_c1.active:
                    puzhal_c1.write({'active': True})
                    log['migrated'].append('reactivated: Puzhal under Chennai - I')
                # Deactivate the Chennai-II duplicate
                puzhal_c2 = Jail.with_context(active_test=False).search([
                    ('name', 'ilike', 'Puzhal'),
                    ('jail_type', '=', 'transit_yard'),
                    ('parent_id', '=', chennai2.id),
                    ('active', '=', True),
                ], limit=1)
                if puzhal_c2:
                    puzhal_c2.write({'active': False})
                    log['deactivated'].append('dedup: Puzhal transit_yard under Chennai - II')

            # Dedup Poonamallee (Men) — keep active record, deactivate extras
            poonamallee_recs = Jail.with_context(active_test=False).search([
                ('name', '=', 'Poonamallee (Men)'),
                ('jail_type', '=', 'special_sub_jail'),
            ])
            if len(poonamallee_recs) > 1:
                active_pm = poonamallee_recs.filtered(lambda r: r.active)
                inactive_pm = poonamallee_recs.filtered(lambda r: not r.active)
                if active_pm:
                    # Keep the first active one, deactivate all others
                    keep = active_pm[0]
                    for dup in active_pm[1:]:
                        dup.write({'active': False})
                        log['deactivated'].append(f'dedup: Poonamallee (Men) id={dup.id} (kept id={keep.id})')
                else:
                    # No active record — activate the first, deactivate rest
                    poonamallee_recs[0].write({'active': True})
                    for dup in poonamallee_recs[1:]:
                        if dup.active:
                            dup.write({'active': False})
                            log['deactivated'].append(f'dedup: Poonamallee (Men) id={dup.id}')

            # Deactivate duplicate inactive women sub-jails (keep active ones only)
            dedup_women = ['Paramakudi', 'Thuckalay', 'Nilakottai', 'Kokkirakulam (Women)']
            for dname in dedup_women:
                dupes = Jail.with_context(active_test=False).search([
                    ('name', '=', dname), ('hierarchy_type', '=', 'women'),
                    ('active', '=', False),
                ])
                if dupes:
                    dupes.write({'active': False})  # already inactive, just confirm

            request.env.cr.commit()

            return {
                'success': True,
                'summary': {
                    'renamed':     len(log['renamed']),
                    'migrated':    len(log['migrated']),
                    'deactivated': len(log['deactivated']),
                    'created':     len(log['created']),
                    'skipped':     len(log['skipped']),
                    'errors':      len(log['errors']),
                },
                'detail': log,
                'stats': {
                    'spw':          Jail.search_count([('jail_type', '=', 'spw')]),
                    'women_sj':     Jail.search_count([('jail_type', '=', 'women_sub_jail')]),
                    'active_total': Jail.search_count([]),
                    'closed_total': Jail.with_context(active_test=False).search_count([('active', '=', False)]),
                    'grand_total':  Jail.with_context(active_test=False).search_count([]),
                },
            }

        except Exception as exc:
            _logger.exception('POST /api/admin/sync-hierarchy failed: %s', exc)
            return {'success': False, 'error': str(exc)}

    # ── Diagnostic endpoint ────────────────────────────────────────────────────

    @http.route('/api/admin/diagnose', methods=['POST'], auth='none', type='json', csrf=False)
    def diagnose_women_records(self, **kwargs):
        """
        POST /api/admin/diagnose
        Body: { "secret": "tnpd-diag-2025" }

        Returns raw state of all women/SPW/transit/special records in DB.
        Use to debug why SPW shows 0 after sync. Remove after DEV sync confirmed.
        """
        DIAG_SECRET = 'tnpd-diag-2025'
        body = request.get_json_data() or {}
        if body.get('secret') != DIAG_SECRET:
            return {'success': False, 'error': 'Unauthorized'}

        Jail = request.env['prison.jail'].sudo()
        try:
            women_types = ('spw', 'women_sub_jail', 'special_sub_jail', 'transit_yard', 'open_air_jail', 'farm_jail')
            recs = Jail.with_context(active_test=False).search([
                '|', ('jail_type', 'in', women_types),
                     ('hierarchy_type', '=', 'women'),
            ])
            records = []
            for r in recs:
                records.append({
                    'id': r.id,
                    'name': r.name,
                    'jail_type': r.jail_type,
                    'hierarchy_type': r.hierarchy_type,
                    'active': r.active,
                    'parent': r.parent_id.name if r.parent_id else None,
                })
            stats = {
                'spw_active':   Jail.search_count([('jail_type', '=', 'spw')]),
                'spw_inactive': Jail.with_context(active_test=False).search_count([
                    ('jail_type', '=', 'spw'), ('active', '=', False)]),
                'spw_total':    Jail.with_context(active_test=False).search_count([('jail_type', '=', 'spw')]),
                'women_hier_total': Jail.with_context(active_test=False).search_count([
                    ('hierarchy_type', '=', 'women')]),
                'active_total': Jail.search_count([]),
                'grand_total':  Jail.with_context(active_test=False).search_count([]),
            }
            return {'success': True, 'stats': stats, 'records': records}
        except Exception as exc:
            _logger.exception('POST /api/admin/diagnose failed: %s', exc)
            return {'success': False, 'error': str(exc)}

    # ══════════════════════════════════════════════════════════════════════════
    # DEV DATA CONSISTENCY FIX — Prison Hierarchy & Closed Prison Validation
    # Reference-dataset-driven sync. Run export on LOCAL (source of truth),
    # POST the snapshot to /sync on DEV. Remove all three endpoints after sync.
    # ══════════════════════════════════════════════════════════════════════════

    _PHX_SECRET = 'tnpd-phx-2025'

    @http.route('/api/admin/prison-hierarchy/export',
                methods=['GET'], auth='none', type='http', csrf=False)
    def prison_hierarchy_export(self, **kwargs):
        """
        GET /api/admin/prison-hierarchy/export?secret=tnpd-phx-2025

        Dumps the full canonical prison hierarchy from the LIVE database
        (LOCAL = source of truth). Output feeds /prison-hierarchy/sync on DEV.

        Each record carries its match key (name, jail_type, hierarchy_type),
        its parent's match coordinates (parent_name, parent_type), and the
        visibility flags (is_closed, active, sequence).
        """
        if kwargs.get('secret') != self._PHX_SECRET:
            return request.make_response(
                json.dumps({'success': False, 'error': 'Unauthorized'}),
                headers=[('Content-Type', 'application/json')], status=401)

        Jail = request.env['prison.jail'].sudo()
        try:
            recs = Jail.with_context(active_test=False).search(
                [], order='sequence, name')
            records = []
            for r in recs:
                records.append({
                    'name':           r.name,
                    'jail_type':      r.jail_type,
                    'hierarchy_type': r.hierarchy_type,
                    'sequence':       r.sequence,
                    'is_closed':      r.is_closed,
                    'active':         r.active,
                    'parent_name':    r.parent_id.name if r.parent_id else None,
                    'parent_type':    r.parent_id.jail_type if r.parent_id else None,
                })
            payload = {
                'success': True,
                'count': len(records),
                'records': records,
            }
            return request.make_response(
                json.dumps(payload),
                headers=[('Content-Type', 'application/json')])
        except Exception as exc:
            _logger.exception('GET /api/admin/prison-hierarchy/export failed: %s', exc)
            return request.make_response(
                json.dumps({'success': False, 'error': str(exc)}),
                headers=[('Content-Type', 'application/json')], status=500)

    @http.route('/api/admin/prison-hierarchy/sync',
                methods=['POST'], auth='none', type='json', csrf=False)
    def prison_hierarchy_sync(self, **kwargs):
        """
        POST /api/admin/prison-hierarchy/sync
        Body: {
            "secret": "tnpd-phx-2025",
            "reference": { "records": [ ...export payload... ] },
            "dry_run": false        # true = validate + report only, no writes
        }

        Reconciles DEV prison data against the LOCAL reference snapshot:
          1. Creates missing records (incl. closed) with correct parent linkage.
          2. Repairs invalid / missing parent mappings to match LOCAL.
          3. Restores Closed Prison visibility (is_closed + active=True).
          4. Resolves duplicates (deactivate extras — never deletes; no data loss).
          5. Re-homes / flags orphan records.
          6. Emits a full validation report + before/after counts.

        Match key: (name, jail_type, hierarchy_type).
        Parent resolved by (parent_name, parent_type), preferring the active row.
        """
        body = request.get_json_data() or {}
        if body.get('secret') != self._PHX_SECRET:
            return {'status': 'UNAUTHORIZED', 'error': 'Unauthorized'}

        reference = (body.get('reference') or {})
        ref_records = reference.get('records') or []
        ref_source = 'request_body'
        # Fall back to the reference snapshot embedded in the module
        # (data/local_hierarchy_reference.json) so DEV can sync with just
        # the secret — no need to pipe the payload over the wire.
        if not ref_records:
            try:
                ref_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    'data', 'local_hierarchy_reference.json')
                with open(ref_path, 'r', encoding='utf-8-sig') as fh:
                    embedded = json.load(fh)
                ref_records = embedded.get('records') or []
                ref_source = 'embedded_snapshot'
            except Exception as exc:
                _logger.exception('Could not load embedded reference: %s', exc)
        if not ref_records:
            return {'status': 'ERROR',
                    'error': 'No reference.records in body and embedded snapshot missing'}

        dry_run = bool(body.get('dry_run'))
        Jail = request.env['prison.jail'].sudo()
        PARENT_TYPES = ('central_jail', 'spw')

        report = {
            'missing_parent_mappings': [],
            'invalid_parent_mappings': [],
            'duplicate_records':       [],
            'orphan_records':          [],
            'corrected_records':       [],
            'created_records':         [],
            'closed_restored':         [],
        }

        def key(name, jail_type, hierarchy_type):
            return (name, jail_type, hierarchy_type)

        try:
            # ── Snapshot BEFORE ───────────────────────────────────────────────
            before = {
                'total':       Jail.with_context(active_test=False).search_count([]),
                'active':      Jail.search_count([]),
                'closed':      Jail.with_context(active_test=False).search_count([('is_closed', '=', True)]),
                'central':     Jail.search_count([('jail_type', '=', 'central_jail')]),
                'spw':         Jail.search_count([('jail_type', '=', 'spw')]),
            }

            # ── Index DEV records by match key ────────────────────────────────
            dev_all = Jail.with_context(active_test=False).search([])
            dev_by_key = {}
            for r in dev_all:
                dev_by_key.setdefault(
                    key(r.name, r.jail_type, r.hierarchy_type), []).append(r)

            # Resolve a parent record in DEV by (name, type); prefer active row.
            def resolve_parent(parent_name, parent_type):
                if not parent_name:
                    return None
                cands = Jail.with_context(active_test=False).search(
                    [('name', '=', parent_name), ('jail_type', '=', parent_type)])
                if not cands:
                    return None
                active_one = cands.filtered(lambda c: c.active)
                return (active_one[0] if active_one else cands[0])

            # Pre-compute canonical active state per key: active if ANY ref is
            # active or closed (so an inactive duplicate doesn't clobber a good one).
            ref_active_by_key = {}
            for _r in ref_records:
                _k = key(_r.get('name'), _r.get('jail_type'),
                         _r.get('hierarchy_type') or 'general')
                _rc = bool(_r.get('is_closed'))
                _ra = True if _rc else bool(_r.get('active', True))
                ref_active_by_key[_k] = ref_active_by_key.get(_k, False) or _ra

            # ── PASS 1: ensure every reference record exists & is correct ─────
            # Process parents (central_jail, spw) before children so parent
            # lookups succeed even when local names differ from canonical names.
            _SORT_ORDER = {'central_jail': 0, 'spw': 0, 'district_jail': 1}
            ref_records = sorted(
                ref_records,
                key=lambda r: _SORT_ORDER.get(r.get('jail_type'), 2))
            for ref in ref_records:
                name = ref.get('name')
                jtype = ref.get('jail_type')
                htype = ref.get('hierarchy_type') or 'general'
                k = key(name, jtype, htype)
                ref_closed = bool(ref.get('is_closed'))
                # Closed prisons stay active; use canonical active across all dups.
                ref_active = True if ref_closed else ref_active_by_key.get(k, True)

                expected_parent = None
                if jtype not in PARENT_TYPES:
                    expected_parent = resolve_parent(
                        ref.get('parent_name'), ref.get('parent_type'))
                    if ref.get('parent_name') and not expected_parent:
                        report['missing_parent_mappings'].append({
                            'record': name, 'jail_type': jtype,
                            'wanted_parent': ref.get('parent_name'),
                            'wanted_parent_type': ref.get('parent_type'),
                        })

                matches = dev_by_key.get(k, [])

                if not matches:
                    # Skip creation of child-type records that have no resolvable
                    # parent — creating them would trip the model constraint and
                    # abort the entire sync transaction.
                    if jtype not in PARENT_TYPES and not expected_parent:
                        report['missing_parent_mappings'].append({
                            'record': name, 'jail_type': jtype,
                            'wanted_parent': ref.get('parent_name'),
                            'wanted_parent_type': ref.get('parent_type'),
                            'skipped': True,
                        })
                        continue
                    # CREATE missing record — skip if (name, hierarchy_type) is
                    # already taken by a different jail_type (unique constraint).
                    if not dry_run:
                        name_slot_taken = Jail.with_context(active_test=False).search_count(
                            [('name', '=', name), ('hierarchy_type', '=', htype),
                             ('jail_type', '!=', jtype)]) > 0
                        if name_slot_taken:
                            report['missing_parent_mappings'].append({
                                'record': name, 'jail_type': jtype,
                                'skipped': True,
                                'reason': 'name+hierarchy already used by different jail_type',
                            })
                            continue
                        vals = {
                            'name': name, 'jail_type': jtype,
                            'hierarchy_type': htype,
                            'sequence': ref.get('sequence') or 10,
                            'is_closed': ref_closed, 'active': ref_active,
                        }
                        if expected_parent:
                            vals['parent_id'] = expected_parent.id
                        rec = Jail.with_context(active_test=False).create(vals)
                        dev_by_key.setdefault(k, []).append(rec)
                    report['created_records'].append(
                        {'record': name, 'jail_type': jtype, 'hierarchy_type': htype,
                         'closed': ref_closed})
                    if ref_closed:
                        report['closed_restored'].append(
                            {'record': name, 'jail_type': jtype, 'action': 'created'})
                    continue

                # Pick the primary DEV record for this key (prefer active)
                active_matches = [m for m in matches if m.active]
                primary = active_matches[0] if active_matches else matches[0]

                # Validate + fix parent mapping
                if expected_parent and primary.parent_id.id != expected_parent.id:
                    report['invalid_parent_mappings'].append({
                        'record': name, 'jail_type': jtype,
                        'current_parent': primary.parent_id.name or None,
                        'expected_parent': expected_parent.name,
                    })
                    if not dry_run:
                        primary.write({'parent_id': expected_parent.id})
                    report['corrected_records'].append(
                        {'record': name, 'fix': 'parent',
                         'to': expected_parent.name})

                # Validate + fix visibility flags
                flag_updates = {}
                if primary.is_closed != ref_closed:
                    flag_updates['is_closed'] = ref_closed
                if primary.active != ref_active:
                    flag_updates['active'] = ref_active
                if ref.get('sequence') is not None and primary.sequence != ref['sequence']:
                    flag_updates['sequence'] = ref['sequence']
                if flag_updates:
                    if not dry_run:
                        primary.write(flag_updates)
                    report['corrected_records'].append(
                        {'record': name, 'fix': 'flags', 'changes': flag_updates})
                    if ref_closed and ('active' in flag_updates or 'is_closed' in flag_updates):
                        report['closed_restored'].append(
                            {'record': name, 'jail_type': jtype, 'action': 'restored_visibility'})

            # Build set of valid reference keys + their parent coords up-front.
            ref_keys = {key(r.get('name'), r.get('jail_type'),
                            r.get('hierarchy_type') or 'general') for r in ref_records}
            ref_parent_by_key = {}
            for r in ref_records:
                ref_parent_by_key[key(r.get('name'), r.get('jail_type'),
                                      r.get('hierarchy_type') or 'general')] = (
                    r.get('parent_name'), r.get('parent_type'))

            # ── PASS 2: duplicates — SAFE dedup, reference-scoped ─────────────
            # Only collapse leaf child-types that the reference knows about.
            # Never touch parent types; never deactivate a row that has active
            # children (would orphan them). Parent-type dups are reported only.
            LEAF_CHILD_TYPES = (
                'sub_jail', 'women_sub_jail', 'special_sub_jail',
                'open_air_jail', 'farm_jail', 'transit_yard',
            )

            def has_active_children(rec):
                return Jail.search_count(
                    [('parent_id', '=', rec.id), ('active', '=', True)]) > 0

            for k in ref_keys:
                rows = dev_by_key.get(k, [])
                active_rows = [r for r in rows if r.active]
                if len(active_rows) <= 1:
                    continue
                jtype = k[1]
                if jtype not in LEAF_CHILD_TYPES:
                    # Parent / district dups — report for manual review only.
                    for ex in active_rows[1:]:
                        report['duplicate_records'].append({
                            'record': ex.name, 'jail_type': ex.jail_type,
                            'hierarchy_type': ex.hierarchy_type, 'id': ex.id,
                            'action': 'reported_only (parent-type, manual review)',
                        })
                    continue
                # Keep the row that has a parent; deactivate childless extras.
                rows_sorted = sorted(
                    active_rows, key=lambda r: 1 if r.parent_id else 0, reverse=True)
                primary = rows_sorted[0]
                for ex in rows_sorted[1:]:
                    if has_active_children(ex):
                        report['duplicate_records'].append({
                            'record': ex.name, 'jail_type': ex.jail_type, 'id': ex.id,
                            'action': 'kept (has active children — not deactivated)',
                        })
                        continue
                    report['duplicate_records'].append({
                        'record': ex.name, 'jail_type': ex.jail_type, 'id': ex.id,
                        'action': 'deactivated (kept id=%s)' % primary.id,
                    })
                    if not dry_run:
                        ex.write({'active': False})

            # ── PASS 3: orphans — child-type rows with missing/invalid parent ─
            # Report-only for visibility; reparent only when the reference
            # supplies a resolvable parent. Never deactivates (no data loss).
            child_rows = Jail.with_context(active_test=False).search([
                ('jail_type', 'not in', list(PARENT_TYPES)),
                ('active', '=', True),
            ])
            for r in child_rows:
                parent_ok = bool(r.parent_id) and r.parent_id.jail_type in (
                    'central_jail', 'spw', 'district_jail')
                if parent_ok:
                    continue
                k = key(r.name, r.jail_type, r.hierarchy_type)
                fixed = False
                if k in ref_parent_by_key:
                    pn, pt = ref_parent_by_key[k]
                    prec = resolve_parent(pn, pt)
                    if prec:
                        if not dry_run:
                            r.write({'parent_id': prec.id})
                        report['corrected_records'].append(
                            {'record': r.name, 'fix': 'orphan_reparent', 'to': prec.name})
                        fixed = True
                report['orphan_records'].append({
                    'record': r.name, 'jail_type': r.jail_type,
                    'hierarchy_type': r.hierarchy_type,
                    'current_parent': r.parent_id.name if r.parent_id else None,
                    'resolved': fixed,
                })

            if not dry_run:
                request.env.cr.commit()

            # ── Snapshot AFTER ────────────────────────────────────────────────
            after = {
                'total':   Jail.with_context(active_test=False).search_count([]),
                'active':  Jail.search_count([]),
                'closed':  Jail.with_context(active_test=False).search_count([('is_closed', '=', True)]),
                'central': Jail.search_count([('jail_type', '=', 'central_jail')]),
                'spw':     Jail.search_count([('jail_type', '=', 'spw')]),
            }

            hierarchy_issues = (len(report['invalid_parent_mappings'])
                                + len(report['missing_parent_mappings'])
                                + len(report['orphan_records']))
            hierarchy_fixed = len([c for c in report['corrected_records']
                                   if c.get('fix') in ('parent', 'orphan_reparent')])

            return {
                'status': 'DRY_RUN' if dry_run else 'SUCCESS',
                'referenceSource': ref_source,
                'totalPrisonsValidated': len(ref_records),
                'hierarchyIssuesFound':  hierarchy_issues,
                'hierarchyIssuesFixed':  hierarchy_fixed,
                'closedPrisonsRestored': len(report['closed_restored']),
                'orphanRecordsFixed':    len([o for o in report['orphan_records'] if o['resolved']]),
                'duplicatesResolved':    len(report['duplicate_records']),
                'recordsCreated':        len(report['created_records']),
                'before': before,
                'after':  after,
                'report': report,
            }

        except Exception as exc:
            if not dry_run:
                request.env.cr.rollback()
            _logger.exception('POST /api/admin/prison-hierarchy/sync failed: %s', exc)
            return {'status': 'ERROR', 'error': str(exc)}

    # ══════════════════════════════════════════════════════════════════════════
    # DEV VACANCY SYNC — bring prison.vacancy + prison.designation.vacancy in
    # line with LOCAL. Matched by the prison's (name, jail_type, hierarchy_type)
    # and the role's name, since DB ids differ between environments.
    # ══════════════════════════════════════════════════════════════════════════

    @http.route('/api/admin/prison-vacancy/export',
                methods=['GET'], auth='none', type='http', csrf=False)
    def prison_vacancy_export(self, **kwargs):
        """
        GET /api/admin/prison-vacancy/export?secret=tnpd-phx-2025

        Dumps the canonical vacancy dataset from the LIVE database (LOCAL):
          - roles          (name, gender_type, sequence)
          - prison_vacancy (per-prison aggregate totals)
          - designations   (per prison+role figures)
        Feeds /prison-vacancy/sync on DEV.
        """
        if kwargs.get('secret') != self._PHX_SECRET:
            return request.make_response(
                json.dumps({'success': False, 'error': 'Unauthorized'}),
                headers=[('Content-Type', 'application/json')], status=401)
        try:
            Role  = request.env['prison.role'].sudo()
            PV    = request.env['prison.vacancy'].sudo()
            DV    = request.env['prison.designation.vacancy'].sudo()

            roles = [{
                'name': r.name, 'gender_type': r.gender_type,
                'sequence': r.sequence,
            } for r in Role.with_context(active_test=False).search([])]

            prison_vacancy = []
            for v in PV.with_context(active_test=False).search([]):
                j = v.prison_id
                if not j:
                    continue
                prison_vacancy.append({
                    'jail_name': j.name, 'jail_type': j.jail_type,
                    'hierarchy_type': j.hierarchy_type,
                    'sanctioned_strength': v.sanctioned_strength,
                    'occupied_count':      v.occupied_count,
                    'vacancy_count':       v.vacancy_count,
                })

            designations = []
            for d in DV.search([]):
                j = d.prison_id
                if not j or not d.role_id:
                    continue
                designations.append({
                    'jail_name': j.name, 'jail_type': j.jail_type,
                    'hierarchy_type': j.hierarchy_type,
                    'role_name': d.role_id.name,
                    'sanctioned_strength': d.sanctioned_strength,
                    'filled_strength':     d.filled_strength,
                })

            payload = {
                'success': True,
                'counts': {'roles': len(roles),
                           'prison_vacancy': len(prison_vacancy),
                           'designations': len(designations)},
                'roles': roles,
                'prison_vacancy': prison_vacancy,
                'designations': designations,
            }
            return request.make_response(
                json.dumps(payload),
                headers=[('Content-Type', 'application/json')])
        except Exception as exc:
            _logger.exception('GET /api/admin/prison-vacancy/export failed: %s', exc)
            return request.make_response(
                json.dumps({'success': False, 'error': str(exc)}),
                headers=[('Content-Type', 'application/json')], status=500)

    @http.route('/api/admin/prison-vacancy/sync',
                methods=['POST'], auth='none', type='json', csrf=False)
    def prison_vacancy_sync(self, **kwargs):
        """
        POST /api/admin/prison-vacancy/sync
        Body: {
            "secret": "tnpd-phx-2025",
            "reference": { "roles": [...], "prison_vacancy": [...], "designations": [...] },
            "dry_run": false
        }

        Reconciles DEV vacancy data with the LOCAL reference:
          1. Upserts roles (prison.role) by name.
          2. Upserts prison.vacancy by resolved prison_id.
          3. Upserts prison.designation.vacancy by (prison_id, role_id).
        Falls back to the embedded data/local_vacancy_reference.json snapshot
        when no reference is supplied, so DEV syncs with just the secret.
        """
        body = request.get_json_data() or {}
        if body.get('secret') != self._PHX_SECRET:
            return {'status': 'UNAUTHORIZED', 'error': 'Unauthorized'}

        reference = body.get('reference') or {}
        ref_source = 'request_body'
        if not (reference.get('roles') or reference.get('prison_vacancy')
                or reference.get('designations')):
            try:
                ref_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    'data', 'local_vacancy_reference.json')
                with open(ref_path, 'r', encoding='utf-8-sig') as fh:
                    reference = json.load(fh)
                ref_source = 'embedded_snapshot'
            except Exception as exc:
                _logger.exception('Could not load embedded vacancy reference: %s', exc)
        if not (reference.get('roles') or reference.get('prison_vacancy')
                or reference.get('designations')):
            return {'status': 'ERROR',
                    'error': 'No reference in body and embedded snapshot missing'}

        dry_run = bool(body.get('dry_run'))
        Jail = request.env['prison.jail'].sudo()
        Role = request.env['prison.role'].sudo()
        PV   = request.env['prison.vacancy'].sudo()
        DV   = request.env['prison.designation.vacancy'].sudo()

        log = {'roles_created': [], 'roles_skipped': [],
               'vacancy_created': [], 'vacancy_updated': [], 'vacancy_unmatched': [],
               'desig_created': [], 'desig_updated': [], 'desig_unmatched': []}

        def resolve_jail(name, jtype, htype):
            cands = Jail.with_context(active_test=False).search([
                ('name', '=', name), ('jail_type', '=', jtype),
                ('hierarchy_type', '=', htype or 'general')])
            if not cands:
                # Fallback: match by name + type only
                cands = Jail.with_context(active_test=False).search([
                    ('name', '=', name), ('jail_type', '=', jtype)])
            if not cands:
                return None
            active_one = cands.filtered(lambda c: c.active)
            return (active_one[0] if active_one else cands[0])

        try:
            # ── 1. ROLES ──────────────────────────────────────────────────────
            role_by_name = {}
            for r in reference.get('roles', []):
                name = r.get('name')
                if not name:
                    continue
                existing = Role.with_context(active_test=False).search(
                    [('name', '=', name)], limit=1)
                if existing:
                    role_by_name[name] = existing
                    log['roles_skipped'].append(name)
                else:
                    if not dry_run:
                        existing = Role.create({
                            'name': name,
                            'gender_type': r.get('gender_type') or 'both',
                            'sequence': r.get('sequence') or 10,
                        })
                        role_by_name[name] = existing
                    log['roles_created'].append(name)

            # ── 2. PRISON-LEVEL VACANCY ───────────────────────────────────────
            for v in reference.get('prison_vacancy', []):
                jail = resolve_jail(v.get('jail_name'), v.get('jail_type'),
                                    v.get('hierarchy_type'))
                if not jail:
                    log['vacancy_unmatched'].append(
                        f"{v.get('jail_name')} [{v.get('jail_type')}]")
                    continue
                vals = {
                    'sanctioned_strength': v.get('sanctioned_strength', 0),
                    'occupied_count':      v.get('occupied_count', 0),
                    'vacancy_count':       v.get('vacancy_count', 0),
                }
                rec = PV.with_context(active_test=False).search(
                    [('prison_id', '=', jail.id)], limit=1)
                if rec:
                    if not dry_run:
                        rec.write(vals)
                    log['vacancy_updated'].append(jail.name)
                else:
                    if not dry_run:
                        PV.create(dict(vals, prison_id=jail.id,
                                       prison_name=jail.name,
                                       prison_type=self._vac_type(jail.jail_type)))
                    log['vacancy_created'].append(jail.name)

            # ── 3. DESIGNATION-WISE VACANCY ───────────────────────────────────
            for d in reference.get('designations', []):
                jail = resolve_jail(d.get('jail_name'), d.get('jail_type'),
                                    d.get('hierarchy_type'))
                role = role_by_name.get(d.get('role_name'))
                if not role:
                    role = Role.with_context(active_test=False).search(
                        [('name', '=', d.get('role_name'))], limit=1)
                if not jail or not role:
                    log['desig_unmatched'].append(
                        f"{d.get('jail_name')} / {d.get('role_name')}")
                    continue
                vals = {
                    'sanctioned_strength': d.get('sanctioned_strength', 0),
                    'filled_strength':     d.get('filled_strength', 0),
                }
                rec = DV.search([('prison_id', '=', jail.id),
                                 ('role_id', '=', role.id)], limit=1)
                if rec:
                    if not dry_run:
                        rec.write(vals)
                    log['desig_updated'].append(f'{jail.name} / {role.name}')
                else:
                    if not dry_run:
                        DV.create(dict(vals, prison_id=jail.id, role_id=role.id))
                    log['desig_created'].append(f'{jail.name} / {role.name}')

            if not dry_run:
                request.env.cr.commit()

            return {
                'status': 'DRY_RUN' if dry_run else 'SUCCESS',
                'referenceSource': ref_source,
                'summary': {
                    'rolesCreated':      len(log['roles_created']),
                    'vacancyCreated':    len(log['vacancy_created']),
                    'vacancyUpdated':    len(log['vacancy_updated']),
                    'vacancyUnmatched':  len(log['vacancy_unmatched']),
                    'designationsCreated': len(log['desig_created']),
                    'designationsUpdated': len(log['desig_updated']),
                    'designationsUnmatched': len(log['desig_unmatched']),
                },
                'detail': log,
            }
        except Exception as exc:
            if not dry_run:
                request.env.cr.rollback()
            _logger.exception('POST /api/admin/prison-vacancy/sync failed: %s', exc)
            return {'status': 'ERROR', 'error': str(exc)}

    @staticmethod
    def _vac_type(jail_type):
        """Map prison.jail.jail_type → prison.vacancy.prison_type."""
        return 'central_prison' if jail_type == 'central_jail' else jail_type

    # ══════════════════════════════════════════════════════════════════════════
    # IMPORT API — idempotent hierarchy repair for DEV deployments
    # ══════════════════════════════════════════════════════════════════════════

    @http.route('/api/admin/import/prison-hierarchy',
                methods=['POST'], auth='none', type='json', csrf=False)
    def import_prison_hierarchy(self, **kwargs):
        """
        POST /api/admin/import/prison-hierarchy
        Body: {
            "secret": "tnpd-phx-2025",
            "repairHierarchy": true,
            "removeDuplicates": true,
            "repairParents": true,
            "defaultPrison": "Chennai - I"
        }

        Idempotent hierarchy repair:
          1. Moves Puzhal (Young Offenders) to Chennai - I (from Chennai - II).
          2. Adds missing Pudukkottai cluster sub-jails under Tiruchirappalli.
          3. Deduplicates Poonamallee (Men) special_sub_jail records.
          4. Runs the embedded hierarchy snapshot sync (prison-hierarchy/sync).

        Safe to run multiple times. No data is deleted — only deactivated.
        """
        body = request.get_json_data() or {}
        if body.get('secret') != self._PHX_SECRET:
            return {'status': 'UNAUTHORIZED', 'error': 'Unauthorized'}

        repair_hierarchy  = body.get('repairHierarchy', True)
        remove_duplicates = body.get('removeDuplicates', True)

        Jail = request.env['prison.jail'].sudo()

        counts = {
            'created': 0,
            'updated': 0,
            'duplicatesRemoved': 0,
            'hierarchyFixed': 0,
            'errors': [],
        }

        def find_jail(name, jail_type=None):
            domain = [('name', '=', name)]
            if jail_type:
                domain.append(('jail_type', '=', jail_type))
            return Jail.with_context(active_test=False).search(domain, limit=1)

        def upsert_jail(name, jail_type, vals):
            existing = Jail.with_context(active_test=False).search(
                [('name', '=', name), ('jail_type', '=', jail_type)], limit=1)
            if existing:
                return existing, False
            clean = {k: v for k, v in vals.items() if not k.startswith('_')}
            rec = Jail.with_context(active_test=False).create(
                dict(clean, name=name, jail_type=jail_type))
            return rec, True

        try:
            # ── Always: ensure canonical central jails are active ────────────
            CANONICAL_CENTRAL = ['Chennai - I', 'Chennai - II', 'Coimbatore',
                                  'Cuddalore', 'Madurai', 'Palayamkottai',
                                  'Salem', 'Tiruchirappalli', 'Vellore']
            for cname in CANONICAL_CENTRAL:
                # Find the best candidate: active, or the one with children/data
                recs = Jail.with_context(active_test=False).search([
                    ('name', '=', cname), ('jail_type', '=', 'central_jail')
                ], order='id asc')
                if not recs:
                    continue
                # Pick the one with the most children; if tie, pick lowest id
                best = max(recs, key=lambda r: len(r.child_ids))
                if not best.active:
                    best.write({'active': True})
                    counts['hierarchyFixed'] += 1
                # Deactivate all others that are still active
                for r in recs.filtered(lambda x: x.id != best.id and x.active):
                    r.write({'active': False})
                    counts['duplicatesRemoved'] += 1

            if repair_hierarchy:
                # ── Fix 1: Puzhal → Chennai - I ───────────────────────────────
                chennai1 = find_jail('Chennai - I', 'central_jail')
                chennai2 = find_jail('Chennai - II', 'central_jail')
                if chennai1 and chennai2:
                    puzhal_c2 = Jail.with_context(active_test=False).search([
                        ('name', 'ilike', 'Puzhal'),
                        ('jail_type', '=', 'transit_yard'),
                        ('parent_id', '=', chennai2.id),
                        ('active', '=', True),
                    ], limit=1)
                    if puzhal_c2:
                        puzhal_c2.write({'parent_id': chennai1.id})
                        counts['hierarchyFixed'] += 1

                    puzhal_c1 = Jail.with_context(active_test=False).search([
                        ('name', 'ilike', 'Puzhal'),
                        ('jail_type', '=', 'transit_yard'),
                        ('parent_id', '=', chennai1.id),
                    ], limit=1)
                    if puzhal_c1 and not puzhal_c1.active:
                        puzhal_c1.write({'active': True})
                        counts['hierarchyFixed'] += 1

                # ── Fix 2: Pudukkottai cluster under Tiruchirappalli ──────────
                trichy = find_jail('Tiruchirappalli', 'central_jail')
                if trichy:
                    # Fix Pudukkottai district_jail itself — must be under Tiruchirappalli
                    pudukkottai_dj = Jail.with_context(active_test=False).search([
                        ('name', '=', 'Pudukkottai'),
                        ('jail_type', '=', 'district_jail'),
                        ('active', '=', True),
                    ], limit=1)
                    if pudukkottai_dj:
                        updates = {}
                        if not pudukkottai_dj.parent_id or pudukkottai_dj.parent_id.id != trichy.id:
                            updates['parent_id'] = trichy.id
                        if updates:
                            pudukkottai_dj.write(updates)
                            counts['updated'] += 1
                            counts['hierarchyFixed'] += 1

                    # Thiruthuraipoondi belongs under Pudukkottai district_jail
                    if pudukkottai_dj:
                        thiruth = Jail.with_context(active_test=False).search([
                            ('name', '=', 'Thiruthuraipoondi'),
                            ('jail_type', '=', 'sub_jail'),
                        ], order='active desc', limit=1)
                        if thiruth:
                            updates = {}
                            if thiruth.parent_id.id != pudukkottai_dj.id:
                                updates['parent_id'] = pudukkottai_dj.id
                            if not thiruth.active:
                                updates['active'] = True
                            if updates:
                                thiruth.write(updates)
                                counts['updated'] += 1
                                counts['hierarchyFixed'] += 1

                    # Sub-jails belong under Pudukkottai district_jail (not directly under Tiruchirappalli)
                    pudukkottai_subs = [
                        ('Aranthangi',        'sub_jail',      40),
                        ('Kumbakonam',        'sub_jail',      50),
                        ('Mannargudi',        'sub_jail',      55),
                        ('Mayiladuthurai',    'sub_jail',      56),
                        ('Nagapattinam',      'district_jail', 57),
                        ('Nannilam',          'sub_jail',      58),
                        ('Papanasam',         'sub_jail',      59),
                        ('Sirkali',           'sub_jail',      60),
                        ('Thanjavur',         'sub_jail',      65),
                        ('Thiruthuraipoondi', 'sub_jail',      70),
                    ]
                    # Use Pudukkottai district_jail as the direct parent
                    target_parent = pudukkottai_dj if pudukkottai_dj else trichy
                    for pname, pjtype, pseq in pudukkottai_subs:
                        _, created = upsert_jail(pname, pjtype, {
                            'hierarchy_type': 'general',
                            'parent_id': target_parent.id,
                            'sequence': pseq,
                            'active': True,
                        })
                        if created:
                            counts['created'] += 1
                        else:
                            rec = find_jail(pname, pjtype)
                            if rec:
                                updates = {}
                                if rec.parent_id.id != target_parent.id:
                                    updates['parent_id'] = target_parent.id
                                if not rec.active:
                                    updates['active'] = True
                                if updates:
                                    rec.write(updates)
                                    counts['updated'] += 1
                                    counts['hierarchyFixed'] += 1

            if remove_duplicates:
                # ── Fix 3b: Dedup Poonamallee (Men) ──────────────────────────
                poonamallee_recs = Jail.with_context(active_test=False).search([
                    ('name', '=', 'Poonamallee (Men)'),
                    ('jail_type', '=', 'special_sub_jail'),
                ])
                if len(poonamallee_recs) > 1:
                    active_pm = poonamallee_recs.filtered(lambda r: r.active)
                    keep = active_pm[0] if active_pm else poonamallee_recs[0]
                    if not keep.active:
                        keep.write({'active': True})
                    for dup in poonamallee_recs.filtered(lambda r: r.id != keep.id and r.active):
                        dup.write({'active': False})
                        counts['duplicatesRemoved'] += 1

                # ── Fix 4: Dedup Thiruthuraipoondi ────────────────────────────
                thiruth_recs = Jail.with_context(active_test=False).search([
                    ('name', '=', 'Thiruthuraipoondi'),
                    ('jail_type', '=', 'sub_jail'),
                    ('active', '=', True),
                ])
                if len(thiruth_recs) > 1:
                    # Keep the one with the highest sequence or first found; deactivate rest
                    keep_t = thiruth_recs[0]
                    for dup in thiruth_recs[1:]:
                        dup.write({'active': False})
                        counts['duplicatesRemoved'] += 1

            request.env.cr.commit()

            return {
                'status': 'SUCCESS',
                'created':          counts['created'],
                'updated':          counts['updated'],
                'duplicatesRemoved': counts['duplicatesRemoved'],
                'hierarchyFixed':   counts['hierarchyFixed'],
                'errors':           counts['errors'],
            }

        except Exception as exc:
            request.env.cr.rollback()
            _logger.exception('POST /api/admin/import/prison-hierarchy failed: %s', exc)
            return {
                'status': 'ERROR',
                'created': 0, 'updated': 0,
                'duplicatesRemoved': 0, 'hierarchyFixed': 0,
                'errors': [str(exc)],
            }

    # ══════════════════════════════════════════════════════════════════════════
    # SYNC DEV → LOCAL CANONICAL STRUCTURE
    # ══════════════════════════════════════════════════════════════════════════

    _LOCAL_CANONICAL = {
        'Chennai - I': [
            'Chengalpattu', 'Kancheepuram', 'Ponneri', 'Poonamallee (Men)',
            'Puzhal (Young Offenders Correctional Facility)', 'Saidapet',
            'Tiruthani', 'Tiruvallur',
        ],
        'Chennai - II': [],
        'Coimbatore': [
            'Avinashi', 'Bhavani', 'Coonoor', 'Dharapuram', 'Erode',
            'Erode @ Gobichettipalayam', 'Gudalur', 'Ooty', 'Palladam',
            'Perundhurai', 'Pollachi', 'Sathiamangalam', 'Singanallur',
            'Tiruppur', 'Udumalaipettai',
        ],
        'Cuddalore': [
            'Chidambaram', 'Gingee', 'Kallakurichi', 'Panruti', 'Thindivanam',
            'Thirukovilur', 'Ulundurpet', 'Villupuram', 'Virudhachalam',
        ],
        'Madurai': [
            'Aruppukottai', 'Dindigul', 'Kodaikanal', 'Melur', 'Mudukulathur',
            'Palani', 'Periakulam', 'Purasaraidaiudaippu', 'Ramanathapuram',
            'Sivagangai', 'Srivilliputhur', 'Theni', 'Thirumangalam',
            'Tiruppathur', 'Usilampatti', 'Uthamapalayam', 'Vedasandur',
            'Virudhunagar',
        ],
        'Palayamkottai': [
            'Ambasamuthiram', 'Kanniyakumari @ Nagercoil', 'Kovilpatti',
            'Kuzhithurai', 'Nanguneri (Men)', 'Sankarankoil', 'Srivaikundam',
            'Tenkasi', 'Thoothukudi @ Perurani',
        ],
        'Salem': [
            'Dharmapuri', 'Harur', 'Hosur', 'Krishnagiri', 'Namakkal',
            'Omalur', 'Salem', 'Sankagiri', 'Thiruchengodu', 'Uthangarai',
        ],
        'Tiruchirappalli': [
            'Ariyalur', 'Jeyankondam', 'Karur', 'Kulithalai', 'Lalgudi',
            'Perambalur', 'Thuraiyur',
        ],
        'Vellore': [
            'Ambur', 'Arakkonam', 'Chengam', 'Cheyyar', 'Gudiyatham',
            'Polur', 'Tirupathur', 'Tiruvannamalai', 'Vandavasi', 'Vaniyambadi',
            'Vellore (Annex)', 'Walajah',
        ],
    }

    # DEV name → LOCAL canonical name (None = just deactivate)
    _DEV_RENAMES = {
        'D.J. Erode @ Gobichettipalayam': 'Erode @ Gobichettipalayam',
        'D.J. Tiruppur':                  'Tiruppur',
        'D.J. Villuppuram':               'Villupuram',
        'D.J. Villupuram':                'Villupuram',
        'D.J. Kanniyakumari @ Nagercoil': 'Kanniyakumari @ Nagercoil',
        'D.J. Dharmapuri':                'Dharmapuri',
        'D.J. Dindigul':                  None,
        'D.J. Ramanathapuram':            None,
        'D.J. Theni':                     None,
        'D.J. Virudhunagar':              None,
    }

    # district_jail records incorrectly surfacing as district_parents — deactivate
    _EXTRA_DISTRICT_PARENTS = [
        'Cuddalore District', 'Tiruchirappalli District', 'Namakkal District',
        'Thanjavur District', 'Tiruppur', 'Dharmapuri', 'Mayiladuthurai District',
        'Thoothukudi District', 'Thiruvarur District',
    ]

    @http.route(
        '/api/admin/sync-local-canonical',
        type='json', auth='none', methods=['POST'], csrf=False,
    )
    def sync_local_canonical(self, **kwargs):
        """
        POST /api/admin/sync-local-canonical
        Body: { "secret": "tnpd-phx-2025" }

        Makes DEV hierarchy match LOCAL canonical structure.
        Idempotent — safe to run multiple times.
        """
        body = request.get_json_data() or {}
        if body.get('secret') != self._PHX_SECRET:
            return {'status': 'UNAUTHORIZED', 'error': 'Unauthorized'}

        Jail = request.env['prison.jail'].sudo()
        log = {'renamed': [], 'deactivated': [], 'fixed': [], 'errors': []}

        try:
            # ── Step 1: Rename D.J. records to canonical names ────────────────
            for dev_name, canonical_name in self._DEV_RENAMES.items():
                dev_recs = Jail.with_context(active_test=False).search(
                    [('name', '=', dev_name), ('active', '=', True)])
                for rec in dev_recs:
                    if canonical_name is None:
                        rec.write({'active': False})
                        log['deactivated'].append(f'extra DJ: {dev_name} id={rec.id}')
                    else:
                        existing = Jail.search([('name', '=', canonical_name),
                                                ('parent_id', '=', rec.parent_id.id)])
                        if existing:
                            rec.write({'active': False})
                            log['deactivated'].append(
                                f'dup DJ: {dev_name} id={rec.id} (kept {existing.id})')
                        else:
                            rec.write({'name': canonical_name})
                            log['renamed'].append(f'{dev_name} -> {canonical_name} id={rec.id}')

            # ── Step 2: Per central prison — deactivate non-canonical children ─
            for central_name, canonical_children in self._LOCAL_CANONICAL.items():
                central = Jail.search([('name', '=', central_name),
                                       ('jail_type', '=', 'central_jail')], limit=1)
                if not central:
                    log['errors'].append(f'Central not found: {central_name}')
                    continue

                canonical_set = {c.lower() for c in canonical_children}
                for child in Jail.search([('parent_id', '=', central.id),
                                          ('active', '=', True)]):
                    if child.name.lower() not in canonical_set:
                        child.write({'active': False})
                        log['deactivated'].append(
                            f'non-canonical: {child.name} (under {central_name})')

                # Reactivate any canonical children that were accidentally deactivated
                for cname in canonical_children:
                    existing = Jail.with_context(active_test=False).search(
                        [('name', '=', cname), ('parent_id', '=', central.id)], limit=1)
                    if existing and not existing.active:
                        existing.write({'active': True})
                        log['fixed'].append(f'reactivated: {cname}')
                    elif not existing:
                        log['errors'].append(f'Missing in DEV: {cname} under {central_name}')

            # ── Step 3: Fix Puzhal → Chennai-I ───────────────────────────────
            chennai1 = Jail.search([('name', '=', 'Chennai - I'),
                                    ('jail_type', '=', 'central_jail')], limit=1)
            if chennai1:
                for pz in Jail.with_context(active_test=False).search([
                    ('name', 'ilike', 'Puzhal'), ('jail_type', '=', 'transit_yard'),
                    ('parent_id', '!=', chennai1.id), ('active', '=', True),
                ]):
                    pz.write({'parent_id': chennai1.id})
                    log['fixed'].append(f'Puzhal moved to Chennai-I id={pz.id}')

            # ── Step 4: Deactivate extra district_parents ─────────────────────
            for dname in self._EXTRA_DISTRICT_PARENTS:
                for rec in Jail.with_context(active_test=False).search(
                        [('name', '=', dname), ('active', '=', True)]):
                    child_count = Jail.search_count([('parent_id', '=', rec.id)])
                    if child_count == 0:
                        rec.write({'active': False})
                        log['deactivated'].append(f'extra district: {dname} id={rec.id}')
                    else:
                        # Re-parent children to the matching central prison, then deactivate
                        parent_central = Jail.search([
                            ('jail_type', '=', 'central_jail'),
                            ('name', 'in', list(self._LOCAL_CANONICAL.keys())),
                        ], limit=1)
                        children = Jail.search([('parent_id', '=', rec.id)])
                        # Move children to the central prison they belong under
                        for child in children:
                            # Find the right central prison from canonical data
                            for cp_name, cp_children in self._LOCAL_CANONICAL.items():
                                if child.name in cp_children:
                                    cp = Jail.search([('name', '=', cp_name),
                                                      ('jail_type', '=', 'central_jail')], limit=1)
                                    if cp:
                                        child.write({'parent_id': cp.id})
                                        log['fixed'].append(
                                            f'reparented {child.name} to {cp_name}')
                                    break
                        rec.write({'active': False})
                        log['deactivated'].append(
                            f'extra district (reparented children): {dname} id={rec.id}')

            # ── Step 5: Dedup Poonamallee (Men) ──────────────────────────────
            pm_recs = Jail.with_context(active_test=False).search(
                [('name', '=', 'Poonamallee (Men)'), ('active', '=', True)])
            if len(pm_recs) > 1:
                keep = pm_recs[0]
                for dup in pm_recs[1:]:
                    dup.write({'active': False})
                    log['deactivated'].append(f'dup Poonamallee id={dup.id}')

            request.env.cr.commit()
            return {
                'status':      'SUCCESS',
                'renamed':     len(log['renamed']),
                'deactivated': len(log['deactivated']),
                'fixed':       len(log['fixed']),
                'errors':      log['errors'],
                'detail':      log,
            }

        except Exception as exc:
            request.env.cr.rollback()
            _logger.exception('POST /api/admin/sync-local-canonical failed: %s', exc)
            return {'status': 'ERROR', 'error': str(exc)}

    # ══════════════════════════════════════════════════════════════════════════
    # VACANCY MASTER DATA SYNC — client Staff-Strength file is single source of
    # truth. Upserts roles + per-role designation vacancy + per-prison aggregate,
    # prunes stale designations on matched prisons, and reports everything.
    # ══════════════════════════════════════════════════════════════════════════

    @http.route('/api/vacancy/sync-master-data',
                methods=['POST'], auth='none', type='json', csrf=False)
    def vacancy_sync_master_data(self, **kwargs):
        """
        POST /api/vacancy/sync-master-data
        Body: {
            "secret": "tnpd-phx-2025",
            "reference": { "roles": [...], "facilities": [...] },   # optional
            "dry_run": false,
            "prune": true     # remove stale designations on matched prisons
        }

        Reference defaults to embedded data/local_master_vacancy_reference.json
        (derived from the client master file). Validates and upserts:
          - prison.role (by name)
          - prison.designation.vacancy by (prison_id, role_id) — sanctioned/filled
          - prison.vacancy aggregate per prison (sum of master posts)
        Prunes designation rows on matched prisons that are absent from master.
        Returns the spec sync summary + validation/correction report.
        """
        body = request.get_json_data() or {}
        if body.get('secret') != self._PHX_SECRET:
            return {'status': 'UNAUTHORIZED', 'error': 'Unauthorized'}

        reference = body.get('reference') or {}
        ref_source = 'request_body'
        if not reference.get('facilities'):
            try:
                ref_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    'data', 'local_master_vacancy_reference.json')
                with open(ref_path, 'r', encoding='utf-8-sig') as fh:
                    reference = json.load(fh)
                ref_source = 'embedded_snapshot'
            except Exception as exc:
                _logger.exception('Could not load master vacancy reference: %s', exc)
        if not reference.get('facilities'):
            return {'status': 'ERROR',
                    'error': 'No reference.facilities in body and embedded snapshot missing'}

        dry_run = bool(body.get('dry_run'))
        prune = body.get('prune', True)
        Jail = request.env['prison.jail'].sudo()
        Role = request.env['prison.role'].sudo()
        PV   = request.env['prison.vacancy'].sudo()
        DV   = request.env['prison.designation.vacancy'].sudo()

        rep = {
            'roles_created': [], 'facilities_unmatched': [],
            'designations_created': 0, 'designations_updated': 0,
            'designations_pruned': 0, 'aggregates_updated': 0,
            'aggregates_created': 0, 'validation_failures': [],
            'duplicates_merged': 0,
        }

        def resolve_jail(name, jtype, htype):
            cands = Jail.with_context(active_test=False).search([
                ('name', '=', name), ('jail_type', '=', jtype),
                ('hierarchy_type', '=', htype or 'general')])
            if not cands:
                cands = Jail.with_context(active_test=False).search([
                    ('name', '=', name), ('jail_type', '=', jtype)])
            if not cands:
                return None
            act = cands.filtered(lambda c: c.active)
            return (act[0] if act else cands[0])

        try:
            total_master_rows = sum(len(f.get('posts', [])) for f in reference['facilities'])

            # ── 1. ROLES upsert (create all from master) ──────────────────────
            role_by_name = {}
            master_role_names = {(r.get('name') or '').strip()
                                 for r in reference.get('roles', []) if r.get('name')}
            for r in reference.get('roles', []):
                nm = (r.get('name') or '').strip()
                if not nm:
                    continue
                existing = Role.with_context(active_test=False).search(
                    [('name', '=', nm)], limit=1)
                if existing:
                    role_by_name[nm] = existing
                else:
                    if not dry_run:
                        existing = Role.create({
                            'name': nm, 'gender_type': r.get('gender_type') or 'both'})
                        role_by_name[nm] = existing
                    rep['roles_created'].append(nm)

            # ── 2. FACILITIES — designations + aggregate ──────────────────────
            for fac in reference['facilities']:
                jail = resolve_jail(fac.get('jail_name'), fac.get('jail_type'),
                                    fac.get('hierarchy_type'))
                if not jail:
                    rep['facilities_unmatched'].append(
                        f"{fac.get('jail_name')} [{fac.get('jail_type')}]")
                    rep['validation_failures'].append(
                        {'facility': fac.get('jail_name'), 'reason': 'prison not found'})
                    continue

                # Pre-aggregate posts per role (sum exact-duplicate roles), validate
                role_totals = {}     # role_id -> [sanctioned, filled]
                agg_s = agg_f = 0
                for p in fac.get('posts', []):
                    s, f = p.get('sanctioned'), p.get('filled')
                    if s is None or s < 0 or f is None or f < 0:
                        rep['validation_failures'].append(
                            {'facility': jail.name, 'role': p.get('role'),
                             'reason': 'invalid counts'})
                        continue
                    pname = (p.get('role') or '').strip()
                    role = role_by_name.get(pname)
                    if not role:
                        role = Role.with_context(active_test=False).search(
                            [('name', '=', pname)], limit=1) or None
                    if role:
                        rkey = role.id
                    elif dry_run and pname in master_role_names:
                        rkey = ('new', pname)   # would be created in a real run
                    else:
                        rep['validation_failures'].append(
                            {'facility': jail.name, 'role': p.get('role'),
                             'reason': 'role unresolved'})
                        continue
                    if rkey in role_totals:
                        rep['duplicates_merged'] += 1
                    rt = role_totals.setdefault(rkey, [0, 0])
                    rt[0] += s
                    rt[1] += f
                    agg_s += s
                    agg_f += f

                # Upsert designation vacancy per role
                real_role_ids = []
                for rid, (s, f) in role_totals.items():
                    if isinstance(rid, tuple):          # ('new', name) — dry-run only
                        rep['designations_created'] += 1
                        continue
                    real_role_ids.append(rid)
                    rec = DV.search([('prison_id', '=', jail.id),
                                     ('role_id', '=', rid)], limit=1)
                    if rec:
                        if rec.sanctioned_strength != s or rec.filled_strength != f:
                            if not dry_run:
                                rec.write({'sanctioned_strength': s, 'filled_strength': f})
                            rep['designations_updated'] += 1
                    else:
                        if not dry_run:
                            DV.create({'prison_id': jail.id, 'role_id': rid,
                                       'sanctioned_strength': s, 'filled_strength': f})
                        rep['designations_created'] += 1

                # Prune stale designations (roles not in master for this prison).
                # Skip in dry-run when new roles exist (ids unknown → would over-report).
                if prune and role_totals and not (dry_run and len(real_role_ids) != len(role_totals)):
                    stale = DV.search([('prison_id', '=', jail.id),
                                       ('role_id', 'not in', real_role_ids)])
                    if stale:
                        rep['designations_pruned'] += len(stale)
                        if not dry_run:
                            stale.unlink()

                # Aggregate prison.vacancy = sum of master posts
                vals = {'sanctioned_strength': agg_s, 'occupied_count': agg_f,
                        'vacancy_count': max(0, agg_s - agg_f)}
                pv = PV.with_context(active_test=False).search(
                    [('prison_id', '=', jail.id)], limit=1)
                if pv:
                    if not dry_run:
                        pv.write(vals)
                    rep['aggregates_updated'] += 1
                else:
                    if not dry_run:
                        PV.create(dict(vals, prison_id=jail.id, prison_name=jail.name,
                                       prison_type=self._vac_type(jail.jail_type)))
                    rep['aggregates_created'] += 1

            if not dry_run:
                request.env.cr.commit()

            final_dv = DV.search_count([])
            final_pv = PV.search_count([])

            return {
                'status': 'DRY_RUN' if dry_run else 'SUCCESS',
                'referenceSource': ref_source,
                'syncSummary': {
                    'totalMasterRecords':      total_master_rows,
                    'facilitiesInMaster':      len(reference['facilities']),
                    'facilitiesMatched':       len(reference['facilities']) - len(rep['facilities_unmatched']),
                    'facilitiesUnmatched':     len(rep['facilities_unmatched']),
                    'rolesCreated':            len(rep['roles_created']),
                    'designationsAdded':       rep['designations_created'],
                    'designationsUpdated':     rep['designations_updated'],
                    'staleDesignationsRemoved': rep['designations_pruned'],
                    'duplicateRowsMerged':     rep['duplicates_merged'],
                    'aggregatesUpdated':       rep['aggregates_updated'] + rep['aggregates_created'],
                    'validationFailures':      len(rep['validation_failures']),
                    'finalDesignationCount':   final_dv,
                    'finalVacancyCount':       final_pv,
                },
                'unmatchedFacilities': rep['facilities_unmatched'],
                'validationFailureSample': rep['validation_failures'][:25],
            }
        except Exception as exc:
            if not dry_run:
                request.env.cr.rollback()
            _logger.exception('POST /api/vacancy/sync-master-data failed: %s', exc)
            return {'status': 'ERROR', 'error': str(exc)}

