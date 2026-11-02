# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Admin notifications
===================

The notices of the Admin login (the bell): a transfer order letter that was addressed or
copied to the user (Superintendent, DIG, IG, DSP). Officers' notices are in the Employee login.

GET  /api/admin/notifications?limit=20&unread=1    this user's notices + unread count
POST /api/admin/notifications/<id>/read            mark one read
POST /api/admin/notifications/read-all             mark all read
"""

import json

from odoo import http
from odoo.http import request

_MAX_LIMIT = 50


class AdminNotificationApiController(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')], status=status,
        )

    def _user(self):
        """(user, None) for a signed-in internal user, else (None, error response)."""
        uid = request.session.uid
        if not uid:
            return None, self._json({'success': False, 'message': 'Authentication required'}, 401)
        user = request.env['res.users'].sudo().browse(uid)
        if user.share:
            return None, self._json({'success': False, 'message': 'Admin login required'}, 403)
        return user, None

    @http.route('/api/admin/notifications', auth='none', type='http', methods=['GET'], csrf=False)
    def list_notifications(self, **kw):
        user, err = self._user()
        if err:
            return err
        try:
            limit = max(1, min(_MAX_LIMIT, int(kw.get('limit', 20))))
        except (TypeError, ValueError):
            limit = 20
        Notification = request.env['tnpd.notification'].sudo()
        domain = [('user_id', '=', user.id)]
        shown = domain + ([('is_read', '=', False)] if kw.get('unread') in ('1', 'true') else [])
        return self._json({
            'success': True,
            'unread_count': Notification.search_count(domain + [('is_read', '=', False)]),
            'notifications': [{
                'id': n.id,
                'message': n.message,
                'sent_date': n.sent_date,
                'is_read': n.is_read,
                'notification_type': n.notification_type,
                'order_id': n.order_id.id if n.order_id.state in ('issued', 'cancelled') else None,
                'order_state': n.order_id.state or '',
                'order_number': n.order_id.name or '',
            } for n in Notification.search(shown, limit=limit)],
        })

    @http.route('/api/admin/notifications/<int:notification_id>/read', auth='none', type='http',
                methods=['POST'], csrf=False, readonly=False)
    def mark_read(self, notification_id, **_kw):
        user, err = self._user()
        if err:
            return err
        note = request.env['tnpd.notification'].sudo().search(
            [('id', '=', notification_id), ('user_id', '=', user.id)])
        if not note:
            return self._json({'success': False, 'message': 'Notification not found'}, 404)
        note.mark_as_read()
        return self._json({'success': True})

    @http.route('/api/admin/notifications/read-all', auth='none', type='http',
                methods=['POST'], csrf=False, readonly=False)
    def mark_all_read(self, **_kw):
        user, err = self._user()
        if err:
            return err
        request.env['tnpd.notification'].sudo().search(
            [('user_id', '=', user.id), ('is_read', '=', False)]).mark_as_read()
        return self._json({'success': True})
