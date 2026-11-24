# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Applicant ranking and suggestion (admin)
========================================

GET /api/transfer/suggestions?request_id=<id>

When several officers ask for the same posting, rank them and suggest the eligible ones.
It only recommends; approval stays with the admin.

Who is compared: the pending requests of the same designation whose first choice is the same
posting (the sub-jail, else the district jail, else the central prison) as the request asked
about. They are ranked by weighted reason score, then years at the current jail, then total
service, then the date of the request. As many eligible applicants are suggested as the
posting has vacancies (one if the vacancy figure is not known). Eligibility uses the
thresholds in the system parameters ``tnpd.transfer.min_service_years`` and
``tnpd.transfer.min_current_jail_years`` (0 = not checked, until the client confirms them).
"""

import json
import logging

from odoo import http
from odoo.http import request

from ..scope import scoped

_logger = logging.getLogger(__name__)


class TransferSuggestionApiController(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')], status=status,
        )

    def _admin(self):
        uid = request.session.uid
        if not uid:
            return None, self._json({'success': False, 'message': 'Authentication required'}, 401)
        user = request.env['res.users'].sudo().browse(uid)
        if user.share or not (user.has_group('base.group_system') or user.has_group('base.group_erp_manager')):
            return None, self._json({'success': False, 'message': 'Admin privileges required'}, 403)
        return uid, None

    @staticmethod
    def _posting(rec):
        """The most specific place the request asks for."""
        return rec.requested_sub_jail or rec.requested_district_jail or rec.requested_central_prison

    @http.route('/api/transfer/suggestions', auth='none', type='http', methods=['GET'], csrf=False)
    def suggestions(self, **kw):
        uid, err = self._admin()
        if err:
            return err
        try:
            request_id = int(kw.get('request_id'))
        except (TypeError, ValueError):
            return self._json({'success': False, 'message': 'request_id is required'}, 400)

        Request = scoped(request.env, 'transfer.approval.request')
        rec = Request.search([('id', '=', request_id)])
        if not rec:
            return self._json({'success': False, 'message': 'Request not found'}, 404)
        posting = self._posting(rec)
        if not posting:
            return self._json({'success': True, 'request_id': rec.id, 'posting': None, 'applicants': [],
                               'note': 'This request does not name a posting.'})

        designation = rec.employee_id.x_designation
        peers = Request.search([('state', '=', 'pending'), ('transfer_type', '=', 'request')])
        peers = peers.filtered(
            lambda r: self._posting(r) == posting and r.employee_id.x_designation == designation
        ) | rec  # the request asked about is always in the list, whatever its state

        vacancy = request.env['prison.vacancy'].sudo().search([('prison_id', '=', posting.id)], limit=1)
        slots = vacancy.vacancy_count if vacancy else None

        ranked = sorted(peers, key=lambda r: (
            -r.reason_score, -r.service_years_current, -r.service_years_total, r.create_date or r.id))
        suggested_left = 1 if slots is None else max(slots, 0)
        applicants = []
        for position, r in enumerate(ranked, start=1):
            eligibility = r._eligibility()
            is_suggested = bool(eligibility['eligible'] and r.state == 'pending' and suggested_left > 0)
            if is_suggested:
                suggested_left -= 1
            applicants.append({
                'rank': position,
                'request_id': r.id,
                'is_this_request': r == rec,
                'state': r.state,
                'employee': {
                    'id': r.employee_id.id, 'name': r.employee_id.name or '',
                    'employee_id': r.employee_id.x_employee_code or '',
                    'designation': r.employee_id.x_designation or '',
                },
                'reasons': r._reasons_payload(),
                'score': r.reason_score,
                'service_years_total': r.service_years_total,
                'service_years_current': r.service_years_current,
                'eligible': eligibility['eligible'],
                'eligibility_checks': eligibility['checks'],
                'suggested': is_suggested,
            })
        return self._json({
            'success': True,
            'request_id': rec.id,
            'posting': {'id': posting.id, 'name': posting.name},
            'designation': designation or '',
            'vacancies': slots,
            'applicants': applicants,
            'note': 'A recommendation only: approval stays with the admin.',
        })
