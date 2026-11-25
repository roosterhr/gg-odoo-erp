# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Institution master: one institution with everything linked to it
================================================================

GET /api/jails/<id>/overview
    One institution with its staff, vacancy, requests and transfers (and the jails under it).
    Optional: ?staff_limit=50&q=<name or code>. Officers, requests and transfers follow the
    caller's area, as everywhere else. Closed institutions are included and say so.
"""

import json
import logging

from odoo import http
from odoo.http import request

from ..scope import scoped

_logger = logging.getLogger(__name__)


class JailMasterApiController(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')], status=status,
        )

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status)

    def _admin(self):
        uid = request.session.uid
        if not uid:
            return None, self._err('Authentication required', 401)
        user = request.env['res.users'].sudo().browse(uid)
        if user.share or not (user.has_group('base.group_system') or user.has_group('base.group_erp_manager')):
            return None, self._err('Admin privileges required', 403)
        return uid, None

    # ── one institution, with everything linked to it ─────────────────────────

    @staticmethod
    def _with_descendants(jail):
        Jail = jail.with_context(active_test=False)
        found, level = jail, jail
        while level:
            level = Jail.search([('parent_id', 'in', level.ids)]) - found
            found |= level
        return found

    @http.route('/api/jails/<int:jail_id>/overview', auth='none', type='http', methods=['GET'], csrf=False)
    def overview(self, jail_id, **kw):
        uid, err = self._admin()
        if err:
            return err
        jail = request.env['prison.jail'].sudo().with_context(active_test=False).browse(jail_id).exists()
        if not jail:
            return self._err('Institution not found', 404)
        try:
            limit = max(1, min(200, int(kw.get('staff_limit', 50))))
        except (TypeError, ValueError):
            limit = 50
        ids = self._with_descendants(jail).ids

        Employee = scoped(request.env, 'hr.employee')
        posted = [('active', '=', True), '|', '|', ('x_central_jail_id', 'in', ids),
                  ('x_district_jail_id', 'in', ids), ('x_sub_jail_id', 'in', ids)]
        staff_domain = list(posted)
        if kw.get('q'):
            staff_domain += ['|', ('name', 'ilike', kw['q']), ('x_employee_code', 'ilike', kw['q'])]
        staff = Employee.search(staff_domain, limit=limit, order='name')
        by_designation = {}
        for emp in Employee.search(posted):
            key = emp.x_designation or 'Unassigned'
            by_designation[key] = by_designation.get(key, 0) + 1

        vacancy = request.env['prison.vacancy'].sudo().with_context(active_test=False).search(
            [('prison_id', '=', jail.id)], limit=1)

        Request = scoped(request.env, 'transfer.approval.request')
        places = lambda prefix: ['|', '|', (prefix + 'central_prison', 'in', ids),
                                 (prefix + 'district_jail', 'in', ids), (prefix + 'sub_jail', 'in', ids)]
        incoming = Request.search(places('requested_') + [('state', 'in', ('pending', 'approved'))], order='id desc', limit=30)
        outgoing = Request.search(places('current_') + [('state', 'in', ('pending', 'approved'))], order='id desc', limit=30)

        def fmt(r):
            return {
                'id': r.id, 'state': r.state, 'transfer_type': r.transfer_type,
                'score': r.reason_score,
                'employee': {'id': r.employee_id.id, 'employee_id': r.employee_id.x_employee_code or '',
                             'name': r.employee_id.name or '', 'designation': r.employee_id.x_designation or ''},
                'from': (r.current_sub_jail or r.current_district_jail or r.current_central_prison).name or '',
                'to': (r.requested_sub_jail or r.requested_district_jail or r.requested_central_prison).name or '',
            }

        return self._json({
            'success': True,
            'institution': {
                'id': jail.id, 'name': jail.name, 'jail_type': jail.jail_type,
                'hierarchy_type': jail.hierarchy_type, 'active': jail.active,
                'is_closed': bool(jail.is_closed), 'closed_remarks': jail.closed_remarks or '',
                'parent': {'id': jail.parent_id.id, 'name': jail.parent_id.name} if jail.parent_id else None,
                'children': [{'id': c.id, 'name': c.name, 'jail_type': c.jail_type, 'is_closed': bool(c.is_closed)}
                             for c in jail.child_ids.with_context(active_test=False).filtered('active')],
            },
            'staff': {
                'total': Employee.search_count(posted),
                'by_designation': sorted(({'designation': k, 'count': v} for k, v in by_designation.items()),
                                         key=lambda x: -x['count']),
                'employees': [{
                    'id': e.id, 'employee_id': e.x_employee_code or '', 'name': e.name or '',
                    'designation': e.x_designation or '',
                    'posting': (e.x_sub_jail_id or e.x_district_jail_id or e.x_central_jail_id).name or '',
                } for e in staff],
            },
            'vacancy': {
                'sanctioned': vacancy.sanctioned_strength, 'occupied': vacancy.occupied_count,
                'vacancy': vacancy.vacancy_count,
            } if vacancy else None,
            'requests': {
                'incoming': [fmt(r) for r in incoming if r.state == 'pending'],
                'outgoing': [fmt(r) for r in outgoing if r.state == 'pending'],
            },
            'transfers': {
                'incoming': [fmt(r) for r in incoming if r.state == 'approved'],
                'outgoing': [fmt(r) for r in outgoing if r.state == 'approved'],
            },
        })
