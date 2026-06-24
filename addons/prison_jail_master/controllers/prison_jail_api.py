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
            domain = [('is_closed', '=', True), ('active', '=', True)]
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
                ('jail_type', '!=', 'district_jail'),  # district_jails are legacy nodes, excluded from flat hierarchy
            ]
            if not include_closed:
                child_domain_base.append(('is_closed', '=', False))

            general_data = []
            women_data   = []

            for parent in all_parents:
                p_data = self._format_jail(parent)

                children = Jail.search(
                    [('parent_id', '=', parent.id)] + child_domain_base,
                    order='sequence, name',
                )
                children_formatted = []
                for c in children:
                    c_data = self._format_jail(c)
                    c_data.update(_strength(c.id))
                    children_formatted.append(c_data)

                # Parent totals: aggregate all children
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

                # backward compat keys
                p_data['district_jails'] = [
                    c for c in children_formatted if c['jail_type'] == 'district_jail'
                ]
                p_data['direct_sub_jails'] = [
                    c for c in children_formatted if c['jail_type'] == 'sub_jail'
                ]

                if parent.hierarchy_type == 'women':
                    women_data.append(p_data)
                else:
                    general_data.append(p_data)

            total_children = Jail.search_count(
                [('jail_type', 'not in', list(_PARENT_TYPES)), ('active', '=', True)]
            )

            return self._json_response({
                'success': True,
                'stats': {
                    'central_prisons':  sum(1 for p in all_parents if p.jail_type == 'central_jail'),
                    'spw':              sum(1 for p in all_parents if p.jail_type == 'spw'),
                    'total_children':   total_children,
                    'total':            len(all_parents) + total_children,
                },
                'data':           general_data,
                'women_prisons':  women_data,
                # backward compat key
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
                existing = find(name, jail_type, vals.get('_parent_name'))
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
            if palk:
                spw_m = find('Madurai', 'spw')
                if spw_m:
                    upsert('Nanguneri (Men)', 'special_sub_jail', {'hierarchy_type': 'general', 'parent_id': palk.id, '_parent_name': 'Palayamkottai', 'sequence': 20})

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
            women_types = ('spw', 'women_sub_jail', 'special_sub_jail')
            inactive_women = Jail.with_context(active_test=False).search([
                ('jail_type', 'in', women_types),
                ('hierarchy_type', '=', 'women'),
                ('active', '=', False),
            ])
            if inactive_women:
                inactive_women.write({'active': True})
                for r in inactive_women:
                    log['migrated'].append(f'reactivated: {r.name} [{r.jail_type}]')

            # Also reactivate transit_yard and special_sub_jail general ones
            inactive_special = Jail.with_context(active_test=False).search([
                ('jail_type', 'in', ('transit_yard', 'open_air_jail', 'farm_jail')),
                ('active', '=', False),
            ])
            if inactive_special:
                inactive_special.write({'active': True})
                for r in inactive_special:
                    log['migrated'].append(f'reactivated: {r.name} [{r.jail_type}]')

            # Reactivate Nanguneri (Men) special_sub_jail
            nanguneri = Jail.with_context(active_test=False).search([
                ('name', '=', 'Nanguneri (Men)'), ('jail_type', '=', 'special_sub_jail'),
            ], limit=1)
            if nanguneri and not nanguneri.active:
                nanguneri.write({'active': True})
                log['migrated'].append('reactivated: Nanguneri (Men) [special_sub_jail]')

            # ── 7. FIX women sub-jail parent → correct SPW ───────────────────
            # Map: sub-jail name → SPW name it belongs to
            spw_parent_map = {
                'Cuddalore':           ('Vellore',         'spw'),
                'Villupuram':          ('Vellore',         'spw'),
                'Dharmapuri':          ('Coimbatore',      'spw'),
                'Salem (Women)':       ('Coimbatore',      'spw'),
                'Thiruvarur':          ('Tiruchirappalli', 'spw'),
                'Nilakottai':          ('Madurai',         'spw'),
                'Paramakudi':          ('Madurai',         'spw'),
                'Thuckalay':           ('Madurai',         'spw'),
                'Kokkirakulam (Women)':('Madurai',         'spw'),
            }
            for child_name, (spw_name, spw_type) in spw_parent_map.items():
                spw_rec = Jail.with_context(active_test=False).search(
                    [('name', '=', spw_name), ('jail_type', '=', spw_type)], limit=1)
                if not spw_rec:
                    continue
                child = Jail.with_context(active_test=False).search(
                    [('name', '=', child_name),
                     ('hierarchy_type', '=', 'women'),
                     ('active', '=', True)], limit=1)
                if child and child.parent_id.id != spw_rec.id:
                    child.write({'parent_id': spw_rec.id})
                    log['migrated'].append(
                        f'reparented: {child_name} → {spw_name} SPW')

            # ── 8. DEDUP: remove extra duplicate transit/women records ────────
            # Deactivate old Puzhal under Chennai-I (correct one is under Chennai-II)
            chennai2 = find_parent('Chennai - II', 'central_jail')
            if chennai2:
                old_puzhal = Jail.with_context(active_test=False).search([
                    ('name', 'ilike', 'Puzhal'),
                    ('jail_type', '=', 'transit_yard'),
                    ('parent_id', '!=', chennai2.id),
                    ('active', '=', True),
                ], limit=1)
                if old_puzhal:
                    old_puzhal.write({'active': False})
                    log['deactivated'].append(
                        f'dedup: Puzhal transit_yard under {old_puzhal.parent_id.name}')

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
