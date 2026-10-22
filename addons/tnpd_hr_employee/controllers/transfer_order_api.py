# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Transfer Order API (admin)
==========================

Proceedings (transfer orders) for approved transfers: check the PDF, fill in the
blanks only a person knows, issue it. Issuing numbers the order and notifies the
officers in it; they download it through the employee portal.

Auth: every endpoint needs an admin session (system or ERP manager group).

GET  /api/transfer/orders                    list (state, search, page, limit)
GET  /api/transfer/orders/eligible           approved transfers not yet in an issued order
POST /api/transfer/orders/create             {request_ids: [..]} one transfer = single
                                             letter, several = bulk list of one designation
GET  /api/transfer/orders/<id>               detail with the officers on it
POST /api/transfer/orders/<id>/update        draft only: present, reference_letter,
                                             reference_date, dig_range, lines[ta_da_eligible]
POST /api/transfer/orders/<id>/issue         number it, lock it, notify the officers
POST /api/transfer/orders/<id>/cancel
GET  /api/transfer/orders/<id>/pdf           the letter (a draft carries a DRAFT banner)
"""

import json
import logging

from odoo import fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)

REPORT_NAME = 'tnpd_hr_employee.report_transfer_offer_letter'
_MAX_LIMIT = 100
_UPDATABLE_TEXT = ('present', 'reference_letter', 'dig_range')


def render_order_pdf(env, order):
    """
    The PDF of a transfer order as an HTTP response.

    An issued order is served from the file stored when it was issued, so it never
    changes afterwards; a draft is rendered from the current data.
    """
    report = env['ir.actions.report'].sudo().with_context(force_report_rendering=True)
    pdf, _kind = report._render_qweb_pdf(REPORT_NAME, order.ids)
    filename = 'Transfer_Order_%s.pdf' % ((order.name or 'DRAFT-%s' % order.id).replace('/', '-'))
    return request.make_response(pdf, headers=[
        ('Content-Type', 'application/pdf'),
        ('Content-Length', str(len(pdf))),
        ('Content-Disposition', content_disposition(filename)),
    ])


class TransferOrderAPI(http.Controller):

    # ── helpers ───────────────────────────────────────────────────────────────

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status=status)

    def _body(self):
        try:
            data = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            return None, self._err('Invalid JSON body.')
        if not isinstance(data, dict):
            return None, self._err('Invalid JSON body.')
        return data, None

    def _require_admin(self):
        """(env, None) for an admin session, else (None, error response)."""
        uid = request.session.uid
        if not uid:
            return None, self._err('Authentication required.', status=401)
        user = request.env['res.users'].sudo().browse(uid)
        if not (user.has_group('base.group_system') or user.has_group('base.group_erp_manager')):
            return None, self._err('Admin privileges required.', status=403)
        return request.env(user=uid), None

    def _order(self, env, order_id):
        """(order, None), or (empty, 404 response) when there is no such order."""
        order = env['transfer.order'].sudo().browse(order_id).exists()
        return order, (None if order else self._err('Transfer order not found.', status=404))

    @staticmethod
    def _message(exc):
        return exc.args[0] if exc.args else str(exc)

    def _fmt_line(self, env, transfer):
        """One officer on an order, as the screens show it."""
        row = env['report.tnpd_hr_employee.report_transfer_offer_letter'].sudo()._row(transfer)
        return {
            'request_id': transfer.id,
            'employee_id': transfer.employee_id.id,
            'employee_name': row['name'],
            'employee_code': row['badge_no'],
            'designation': row['designation'],
            'from': row['source'],
            'to': row['target'],
            'transfer_type': transfer.transfer_type,
            'approved_on': row['approved_on'],
            'ta_da_eligible': bool(transfer.ta_da_eligible),
        }

    def _fmt_order(self, env, order, lines=True):
        """An order as JSON; ``lines=False`` leaves out the officers (for lists)."""
        data = {
            'id': order.id,
            'number': order.name or '',
            'display_name': order.display_name,
            'state': order.state,
            'layout': order.layout,
            'order_date': str(order.order_date) if order.order_date else '',
            'present': order.present or '',
            'reference_letter': order.reference_letter or '',
            'reference_date': str(order.reference_date) if order.reference_date else '',
            'dig_range': order.dig_range or '',
            'issued_date': str(order.issued_date) if order.issued_date else '',
            'issued_by': order.issued_by.name or '',
            'officer_count': len(order.transfer_ids),
            'officers': order.transfer_ids.sorted('id').mapped('employee_id.name'),
        }
        if lines:
            data['lines'] = [self._fmt_line(env, t) for t in order.transfer_ids.sorted('id')]
        return data

    # ── list / eligible / detail ──────────────────────────────────────────────

    @http.route('/api/transfer/orders', auth='none', type='http', methods=['GET'], csrf=False)
    def list_orders(self, **kwargs):
        """Orders, newest first. Filters: state, search (number or officer); paged."""
        env, err = self._require_admin()
        if err:
            return err
        try:
            page = max(1, int(kwargs.get('page', 1)))
            limit = max(1, min(_MAX_LIMIT, int(kwargs.get('limit', 20))))
        except (TypeError, ValueError):
            return self._err('Invalid pagination parameter.')
        domain = []
        if kwargs.get('state') in ('draft', 'issued', 'cancelled'):
            domain.append(('state', '=', kwargs['state']))
        if kwargs.get('search'):
            domain += ['|', ('name', 'ilike', kwargs['search']),
                       ('transfer_ids.employee_id.name', 'ilike', kwargs['search'])]
        Order = env['transfer.order'].sudo()
        orders = Order.search(domain, offset=(page - 1) * limit, limit=limit)
        return self._json({
            'success': True, 'page': page, 'limit': limit, 'total': Order.search_count(domain),
            'orders': [self._fmt_order(env, o, lines=False) for o in orders],
        })

    @http.route('/api/transfer/orders/eligible', auth='none', type='http', methods=['GET'], csrf=False)
    def eligible_transfers(self, **kwargs):
        """Approved transfers that can go into a new order (not in an issued one)."""
        env, err = self._require_admin()
        if err:
            return err
        try:
            limit = max(1, min(_MAX_LIMIT, int(kwargs.get('limit', 50))))
        except (TypeError, ValueError):
            return self._err('Invalid limit.')
        issued = env['transfer.order'].sudo().search([('state', '=', 'issued')]).transfer_ids.ids
        domain = [('state', '=', 'approved'), ('id', 'not in', issued)]
        if kwargs.get('search'):
            domain += ['|', ('employee_id.name', 'ilike', kwargs['search']),
                       ('employee_id.x_employee_code', 'ilike', kwargs['search'])]
        designation = (kwargs.get('designation') or '').strip()
        if designation:  # same rule as the order: the role name, else the typed designation
            domain += ['|', ('employee_id.x_role_id.name', '=', designation),
                       '&', ('employee_id.x_role_id', '=', False),
                       ('employee_id.x_designation', '=', designation)]
        transfers = env['transfer.approval.request'].sudo().search(domain, order='id desc', limit=limit)
        result = []
        for transfer in transfers:
            line = self._fmt_line(env, transfer)
            live = transfer._live_order()
            line['order_id'] = live.id if live else None
            result.append(line)
        return self._json({'success': True, 'transfers': result})

    @http.route('/api/transfer/orders/<int:order_id>', auth='none', type='http', methods=['GET'], csrf=False)
    def get_order(self, order_id, **_kw):
        """One order with its officers."""
        env, err = self._require_admin()
        if err:
            return err
        order, err = self._order(env, order_id)
        if err:
            return err
        return self._json({'success': True, 'order': self._fmt_order(env, order)})

    # ── create / update ───────────────────────────────────────────────────────

    @http.route('/api/transfer/orders/create', auth='none', type='http', methods=['POST'],
                csrf=False, readonly=False)
    def create_order(self, **_kw):
        env, err = self._require_admin()
        if err:
            return err
        data, err = self._body()
        if err:
            return err
        try:
            ids = [int(i) for i in (data.get('request_ids') or [])]
        except (TypeError, ValueError):
            return self._err('request_ids must be a list of numbers.')
        if not ids:
            return self._err('request_ids is required.')
        transfers = env['transfer.approval.request'].sudo().browse(ids).exists()
        if len(transfers) != len(set(ids)):
            return self._err('Transfer not found.', status=404)
        try:
            with env.cr.savepoint():
                order = env['transfer.order'].sudo()._draft_for_transfers(transfers)
        except (UserError, ValidationError) as exc:
            return self._err(self._message(exc))
        return self._json({'success': True, 'order': self._fmt_order(env, order)})

    @http.route('/api/transfer/orders/<int:order_id>/update', auth='none', type='http',
                methods=['POST'], csrf=False, readonly=False)
    def update_order(self, order_id, **_kw):
        env, err = self._require_admin()
        if err:
            return err
        order, err = self._order(env, order_id)
        if err:
            return err
        data, err = self._body()
        if err:
            return err
        if order.state != 'draft':
            return self._err('Only a draft order can be edited.')

        # null means "blank", never the text "None"
        vals = {
            f: (str(data[f]).strip() if data[f] is not None else '') or False
            for f in _UPDATABLE_TEXT if f in data
        }
        for field_name in ('reference_date', 'order_date'):
            if field_name in data:
                try:
                    vals[field_name] = fields.Date.to_date(data[field_name]) if data[field_name] else False
                except (TypeError, ValueError):
                    return self._err('%s must be a date (YYYY-MM-DD).' % field_name)
        lines = data.get('lines') or []
        if not isinstance(lines, list) or not all(isinstance(line, dict) for line in lines):
            return self._err('lines must be a list of {request_id, ta_da_eligible}.')
        by_id = {t.id: t for t in order.transfer_ids}
        for line in lines:
            if line.get('request_id') not in by_id:
                return self._err('A line does not belong to this order.')
        try:
            with env.cr.savepoint():
                order.write(vals)
                for line in lines:
                    if 'ta_da_eligible' in line:
                        by_id[line['request_id']].write({'ta_da_eligible': bool(line['ta_da_eligible'])})
        except (UserError, ValidationError) as exc:
            return self._err(self._message(exc))
        return self._json({'success': True, 'order': self._fmt_order(env, order)})

    # ── issue / cancel ────────────────────────────────────────────────────────

    def _transition(self, order_id, action):
        """Run ``action_issue`` or ``action_cancel`` on an order; a refusal changes nothing."""
        env, err = self._require_admin()
        if err:
            return err
        order, err = self._order(env, order_id)
        if err:
            return err
        try:
            with env.cr.savepoint():
                getattr(order, action)()
        except (UserError, ValidationError) as exc:
            return self._err(self._message(exc))
        return self._json({'success': True, 'order': self._fmt_order(env, order)})

    @http.route('/api/transfer/orders/<int:order_id>/issue', auth='none', type='http',
                methods=['POST'], csrf=False, readonly=False)
    def issue_order(self, order_id, **_kw):
        return self._transition(order_id, 'action_issue')

    @http.route('/api/transfer/orders/<int:order_id>/cancel', auth='none', type='http',
                methods=['POST'], csrf=False, readonly=False)
    def cancel_order(self, order_id, **_kw):
        return self._transition(order_id, 'action_cancel')

    # ── PDF ───────────────────────────────────────────────────────────────────

    @http.route('/api/transfer/orders/<int:order_id>/pdf', auth='none', type='http',
                methods=['GET'], csrf=False)
    def order_pdf(self, order_id, **_kw):
        env, err = self._require_admin()
        if err:
            return err
        order, err = self._order(env, order_id)
        if err:
            return err
        if order.state == 'cancelled':
            return self._err('A cancelled order cannot be printed.')
        return render_order_pdf(env, order)
