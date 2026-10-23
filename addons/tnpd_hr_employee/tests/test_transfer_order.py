# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import json
import re
from datetime import date

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from .common import approved_transfer, make_fixtures

REPORT = 'tnpd_hr_employee.report_transfer_offer_letter'


@tagged('post_install', '-at_install')
class TestTransferOrder(TransactionCase):
    """The transfer order record: numbering, issue, lock, notices, bulk rules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Order')
        cls.Order = cls.env['transfer.order']
        cls.Notification = cls.env['tnpd.notification']

    def _transfer(self, officer=None, transfer_type='request'):
        return approved_transfer(self.env, self.fx, officer or self.fx.officers[0], transfer_type)

    # ── creating ──────────────────────────────────────────────────────────────

    def test_approving_a_transfer_gives_it_a_draft_order(self):
        officer = self.fx.officers[0]
        request = self.env['transfer.approval.request'].create({
            'employee_id': officer.id, 'transfer_type': 'request',
            'current_central_prison': self.fx.src_central.id, 'current_sub_jail': self.fx.src_sub.id,
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'requested_by': self.env.user.id, 'state': 'pending',
        })
        self.assertFalse(request.order_ids)
        request._execute_approval(self.env.user, fields.Datetime.now())
        self.assertEqual(request.state, 'approved')
        self.assertEqual(len(request.order_ids), 1)
        self.assertEqual(request.order_ids.state, 'draft')
        self.assertEqual(request.order_ids.layout, 'single')

    def test_only_approved_transfers_can_be_ordered(self):
        pending = self.env['transfer.approval.request'].create({
            'employee_id': self.fx.officers[0].id, 'transfer_type': 'request',
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'requested_by': self.env.user.id, 'state': 'draft',
        })
        with self.assertRaises(ValidationError):
            self.Order.create({'transfer_ids': [(6, 0, pending.ids)]})

    def test_asking_twice_returns_the_same_order(self):
        transfer = self._transfer()
        first = self.Order._draft_for_transfers(transfer)
        self.assertEqual(self.Order._draft_for_transfers(transfer), first)

    def test_ta_da_defaults_follow_the_transfer_type(self):
        self.assertFalse(self._transfer(self.fx.officers[0], 'request').ta_da_eligible)
        self.assertTrue(self._transfer(self.fx.officers[1], 'admin_grounds').ta_da_eligible)

    def test_print_button_opens_the_order_of_an_approved_transfer(self):
        transfer = self._transfer()
        # Odoo 19 opens the PDF in a viewer dialog; the report itself is nested in its context.
        action = transfer.action_print_transfer_order()['context']['report_action']
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'], 'tnpd_hr_employee.report_transfer_offer_letter')
        self.assertEqual(action['context']['active_ids'], transfer.order_ids.ids)
        self.assertEqual(len(transfer.order_ids), 1)

    def test_print_button_refuses_a_transfer_that_is_not_approved(self):
        pending = self.env['transfer.approval.request'].create({
            'employee_id': self.fx.officers[0].id, 'transfer_type': 'request',
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'requested_by': self.env.user.id, 'state': 'draft',
        })
        with self.assertRaises(UserError):
            pending.action_print_transfer_order()

    # ── issue ─────────────────────────────────────────────────────────────────

    def test_issue_numbers_locks_and_notifies_the_officer(self):
        transfer = self._transfer()
        order = self.Order._draft_for_transfers(transfer)
        self.assertFalse(order.name)
        order.action_issue()
        self.assertEqual(order.state, 'issued')
        self.assertRegex(order.name, r'^\d{3}/\d{4}$')
        self.assertEqual(order.issued_by, self.env.user)
        self.assertTrue(order.order_date and order.issued_date)
        notice = self.Notification.search([('order_id', '=', order.id)])
        self.assertEqual(notice.employee_id, self.fx.officers[0])
        self.assertEqual(notice.notification_type, 'transfer_order')
        self.assertIn(order.name, notice.message)
        self.assertEqual(notice.transfer_request_id, transfer)

    def test_an_issued_order_cannot_be_edited_or_deleted(self):
        order = self.Order._draft_for_transfers(self._transfer())
        order.action_issue()
        with self.assertRaises(UserError):
            order.write({'present': 'Someone else'})
        with self.assertRaises(UserError):
            order.unlink()
        with self.assertRaises(UserError):
            order.action_issue()

    def test_an_order_without_transfers_is_not_issued(self):
        with self.assertRaises(UserError):
            self.Order.create({}).action_issue()

    def test_number_pattern_is_configurable(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('tnpd.transfer_order.number_format', '{serial}/{section}/{year}')
        params.set_param('tnpd.transfer_order.section_code', 'P2')
        order = self.Order._draft_for_transfers(self._transfer())
        order.action_issue()
        self.assertRegex(order.name, r'^\d{3}/P2/\d{4}$')

    # ── cancel ────────────────────────────────────────────────────────────────

    def test_cancelling_keeps_the_number_and_lets_the_transfer_be_ordered_again(self):
        transfer = self._transfer()
        first = self.Order._draft_for_transfers(transfer)
        first.action_issue()
        number = first.name
        first.action_cancel()
        self.assertEqual((first.state, first.name), ('cancelled', number))
        cancel_notice = self.Notification.search([('order_id', '=', first.id), ('message', 'like', 'cancelled')])
        self.assertTrue(cancel_notice)

        second = self.Order._draft_for_transfers(transfer)
        self.assertNotEqual(second, first)
        second.action_issue()
        self.assertNotEqual(second.name, number)

    # ── an issued letter is fixed ─────────────────────────────────────────────

    def test_an_issued_letter_does_not_change_after_it_is_issued(self):
        order = self.Order._draft_for_transfers(self._transfer())
        order.action_issue()
        stored = self.env['ir.attachment'].search([('res_model', '=', 'transfer.order'), ('res_id', '=', order.id)])
        self.assertEqual(len(stored), 1)

        report = self.env['ir.actions.report'].with_context(force_report_rendering=True)
        before, _kind = report._render_qweb_pdf(REPORT, order.ids)
        # A promotion and a renamed prison afterwards must not touch the letter.
        self.fx.officers[0].x_designation = 'Jailor'
        self.fx.dst_sub.name = 'Renamed Sub'
        after, _kind = report._render_qweb_pdf(REPORT, order.ids)
        self.assertEqual(before, after)
        self.assertEqual(stored.raw, before)

    def test_a_draft_is_not_frozen(self):
        order = self.Order._draft_for_transfers(self._transfer())
        self.assertFalse(self.env['ir.attachment'].search_count(
            [('res_model', '=', 'transfer.order'), ('res_id', '=', order.id)]))

    def test_ta_da_cannot_change_once_the_order_is_issued(self):
        transfer = self._transfer()
        order = self.Order._draft_for_transfers(transfer)
        transfer.ta_da_eligible = True            # fine while it is a draft
        order.action_issue()
        with self.assertRaises(UserError):
            transfer.write({'ta_da_eligible': False})
        order.action_cancel()
        transfer.write({'ta_da_eligible': False})  # free again after the order is cancelled

    # ── numbering ─────────────────────────────────────────────────────────────

    def test_the_serial_starts_again_each_year(self):
        numbers = []
        for officer, day in ((self.fx.officers[0], date(2031, 1, 5)),
                             (self.fx.officers[1], date(2031, 6, 1)),
                             (self.fx.officers[2], date(2032, 1, 2))):
            order = self.Order._draft_for_transfers(self._transfer(officer))
            order.order_date = day
            order.action_issue()
            numbers.append(order.name)
        self.assertEqual(numbers, ['001/2031', '002/2031', '001/2032'])

    # ── who can read ──────────────────────────────────────────────────────────

    def test_only_hr_users_can_read_orders(self):
        Users = self.env['res.users']
        staff = Users.create({'name': 'Plain Staff', 'login': 'order_plain_staff',
                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        hr = Users.create({'name': 'Hr Staff', 'login': 'order_hr_staff',
                           'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                                 self.env.ref('hr.group_hr_user').id])]})
        self.Order._draft_for_transfers(self._transfer())
        with self.assertRaises(AccessError):
            self.Order.with_user(staff).search([])
        self.assertTrue(self.Order.with_user(hr).search([]))

    # ── bulk ──────────────────────────────────────────────────────────────────

    def test_bulk_order_takes_the_transfers_out_of_their_drafts(self):
        transfers = self.env['transfer.approval.request']
        singles = self.Order
        for officer in self.fx.officers:
            transfer = self._transfer(officer)
            transfers |= transfer
            singles |= self.Order._draft_for_transfers(transfer)
        bulk = self.Order._draft_for_transfers(transfers)
        self.assertEqual(bulk.layout, 'bulk')
        self.assertEqual(bulk.transfer_ids, transfers)
        self.assertEqual(set(singles.mapped('state')), {'cancelled'})   # emptied, so closed

    def test_bulk_order_needs_one_designation(self):
        mixed = self._transfer(self.fx.officers[0]) | self._transfer(self.fx.other_designation)
        with self.assertRaises(ValidationError):
            self.Order._draft_for_transfers(mixed)

    def test_a_transfer_cannot_be_in_two_live_orders(self):
        transfer = self._transfer()
        self.Order._draft_for_transfers(transfer)
        with self.assertRaises(ValidationError):
            self.Order.create({'transfer_ids': [(6, 0, transfer.ids)]})

    def test_a_transfer_in_an_issued_order_cannot_join_a_bulk_order(self):
        issued_transfer = self._transfer(self.fx.officers[0])
        self.Order._draft_for_transfers(issued_transfer).action_issue()
        other = self._transfer(self.fx.officers[1])
        with self.assertRaises(UserError):
            self.Order._draft_for_transfers(issued_transfer | other)


@tagged('post_install', '-at_install')
class TestTransferOrderAPI(HttpCase):
    """/api/transfer/orders (admin) and /api/employee-portal/orders (the officer)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Api')
        Users = cls.env['res.users']
        group_user = cls.env.ref('base.group_user')
        cls.admin = Users.create({
            'name': 'Api Admin', 'login': 'api_admin', 'password': 'Api-Admin-1',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('base.group_erp_manager').id])],
        })
        cls.staff = Users.create({
            'name': 'Api Staff', 'login': 'api_staff', 'password': 'Api-Staff-1',
            'group_ids': [(6, 0, [group_user.id])],
        })
        cls.officer_user = Users.create({
            'name': 'Api Officer', 'login': 'api_officer', 'password': 'Api-Officer-1',
            'group_ids': [(6, 0, [group_user.id])],
        })
        cls.other_user = Users.create({
            'name': 'Api Other', 'login': 'api_other', 'password': 'Api-Other-1',
            'group_ids': [(6, 0, [group_user.id])],
        })
        cls.fx.officers[0].user_id = cls.officer_user
        cls.fx.officers[1].user_id = cls.other_user

    def _call(self, method, path, body=None):
        url = path
        if method == 'POST':
            return self.url_open(url, data=json.dumps(body or {}), headers={'Content-Type': 'application/json'})
        return self.url_open(url)

    def _json(self, response):
        return response.json()

    def _transfer(self, officer):
        return approved_transfer(self.env, self.fx, officer)

    # ── who may call ──────────────────────────────────────────────────────────

    def test_admin_endpoints_need_an_admin(self):
        self.assertEqual(self._call('GET', '/api/transfer/orders').status_code, 401)
        self.authenticate('api_staff', 'Api-Staff-1')
        self.assertEqual(self._call('GET', '/api/transfer/orders').status_code, 403)
        self.assertEqual(self._call('POST', '/api/transfer/orders/create', {'request_ids': [1]}).status_code, 403)

    # ── admin flow ────────────────────────────────────────────────────────────

    def test_admin_creates_edits_and_issues_an_order(self):
        transfer = self._transfer(self.fx.officers[0])
        self.authenticate('api_admin', 'Api-Admin-1')

        created = self._json(self._call('POST', '/api/transfer/orders/create', {'request_ids': [transfer.id]}))
        self.assertTrue(created['success'], created)
        order = created['order']
        self.assertEqual((order['state'], order['layout'], order['number']), ('draft', 'single', ''))
        self.assertEqual(order['lines'][0]['employee_code'], 'API-0001')

        again = self._json(self._call('POST', '/api/transfer/orders/create', {'request_ids': [transfer.id]}))
        self.assertEqual(again['order']['id'], order['id'])

        updated = self._json(self._call('POST', '/api/transfer/orders/%s/update' % order['id'], {
            'present': 'Thiru. Api Present', 'dig_range': 'Madurai',
            'reference_letter': '55/2026', 'reference_date': '2026-04-01',
            'lines': [{'request_id': transfer.id, 'ta_da_eligible': True}],
        }))
        self.assertTrue(updated['success'], updated)
        self.assertEqual(updated['order']['present'], 'Thiru. Api Present')
        self.assertEqual(updated['order']['reference_date'], '2026-04-01')
        self.assertTrue(updated['order']['lines'][0]['ta_da_eligible'])

        pdf = self._call('GET', '/api/transfer/orders/%s/pdf' % order['id'])
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers['Content-Type'], 'application/pdf')

        issued = self._json(self._call('POST', '/api/transfer/orders/%s/issue' % order['id']))
        self.assertTrue(issued['success'], issued)
        self.assertEqual(issued['order']['state'], 'issued')
        self.assertRegex(issued['order']['number'], r'^\d{3}/\d{4}$')

        locked = self._json(self._call('POST', '/api/transfer/orders/%s/update' % order['id'], {'present': 'x'}))
        self.assertFalse(locked['success'])

        listing = self._json(self._call('GET', '/api/transfer/orders?state=issued'))
        self.assertIn(order['id'], [o['id'] for o in listing['orders']])

        cancelled = self._json(self._call('POST', '/api/transfer/orders/%s/cancel' % order['id']))
        self.assertEqual(cancelled['order']['state'], 'cancelled')
        self.assertEqual(self._call('GET', '/api/transfer/orders/%s/pdf' % order['id']).status_code, 400)

    def test_a_bad_request_is_refused_and_changes_nothing(self):
        pending = self.env['transfer.approval.request'].create({
            'employee_id': self.fx.officers[2].id, 'transfer_type': 'request',
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'requested_by': self.env.user.id, 'state': 'draft',
        })
        self.authenticate('api_admin', 'Api-Admin-1')
        result = self._json(self._call('POST', '/api/transfer/orders/create', {'request_ids': [pending.id]}))
        self.assertFalse(result['success'])
        self.assertFalse(self.env['transfer.order'].search([('transfer_ids', '=', pending.id)]))
        self.assertEqual(self._call('GET', '/api/transfer/orders/999999999').status_code, 404)

    def test_eligible_lists_approved_transfers_not_in_an_issued_order(self):
        free = self._transfer(self.fx.officers[0])
        taken = self._transfer(self.fx.officers[1])
        self.env['transfer.order']._draft_for_transfers(taken).action_issue()
        self.authenticate('api_admin', 'Api-Admin-1')
        result = self._json(self._call('GET', '/api/transfer/orders/eligible?limit=100'))
        ids = [t['request_id'] for t in result['transfers']]
        self.assertIn(free.id, ids)
        self.assertNotIn(taken.id, ids)

    def test_designation_filter_is_applied_before_the_limit(self):
        jailor = self._transfer(self.fx.other_designation)        # older
        self._transfer(self.fx.officers[0])                       # newer Grade I
        self._transfer(self.fx.officers[1])
        self.authenticate('api_admin', 'Api-Admin-1')
        result = self._json(self._call('GET', '/api/transfer/orders/eligible?designation=Jailor&limit=1'))
        self.assertEqual([t['request_id'] for t in result['transfers']], [jailor.id])

    def test_null_and_malformed_input_is_handled(self):
        transfer = self._transfer(self.fx.officers[0])
        self.authenticate('api_admin', 'Api-Admin-1')
        order = self._json(self._call('POST', '/api/transfer/orders/create', {'request_ids': [transfer.id]}))['order']
        url = '/api/transfer/orders/%s/update' % order['id']

        result = self._json(self._call('POST', url, {'present': None, 'dig_range': '  '}))
        self.assertTrue(result['success'], result)
        self.assertEqual((result['order']['present'], result['order']['dig_range']), ('', ''))

        for bad in ({'lines': ['x']}, {'lines': 'x'}, {'lines': [{'request_id': 999999999}]}):
            self.assertFalse(self._json(self._call('POST', url, bad))['success'], bad)

    # ── the officer's download ────────────────────────────────────────────────

    def test_an_unreadable_stored_pdf_is_an_error_not_an_empty_download(self):
        order = self.env['transfer.order']._draft_for_transfers(self._transfer(self.fx.officers[0]))
        order.action_issue()
        stored = self.env['ir.attachment'].search([('res_model', '=', 'transfer.order'), ('res_id', '=', order.id)])
        # Emptying the content stands in for a stored file that is missing from the file
        # store: both make the report return an empty PDF (seen on a demo copy).
        stored.raw = b''
        self.authenticate('api_officer', 'Api-Officer-1')
        response = self._call('GET', '/api/employee-portal/orders/%s/pdf' % order.id)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(self._json(response)['success'])

    def test_the_officer_gets_the_letter_as_it_was_issued(self):
        order = self.env['transfer.order']._draft_for_transfers(self._transfer(self.fx.officers[0]))
        order.action_issue()
        self.authenticate('api_officer', 'Api-Officer-1')
        url = '/api/employee-portal/orders/%s/pdf' % order.id
        first = self._call('GET', url).content
        self.assertTrue(first.startswith(b'%PDF'))

        self.fx.officers[0].x_designation = 'Jailor'              # changes nothing on the letter
        self.fx.dst_sub.name = 'Renamed Sub'
        self.assertEqual(self._call('GET', url).content, first)

    def test_officer_downloads_only_their_own_issued_order(self):
        mine = self.env['transfer.order']._draft_for_transfers(self._transfer(self.fx.officers[0]))
        theirs = self.env['transfer.order']._draft_for_transfers(self._transfer(self.fx.officers[1]))
        mine_url = '/api/employee-portal/orders/%s/pdf' % mine.id
        theirs_url = '/api/employee-portal/orders/%s/pdf' % theirs.id

        self.assertEqual(self._call('GET', mine_url).status_code, 401)        # no session

        self.authenticate('api_officer', 'Api-Officer-1')
        self.assertEqual(self._call('GET', mine_url).status_code, 404)        # still a draft

        mine.action_issue()
        theirs.action_issue()
        response = self._call('GET', mine_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        self.assertEqual(self._call('GET', theirs_url).status_code, 404)      # someone else's

        mine.action_cancel()
        self.assertEqual(self._call('GET', mine_url).status_code, 404)        # cancelled

    def test_officer_sees_the_order_in_notifications_and_transfers(self):
        order = self.env['transfer.order']._draft_for_transfers(self._transfer(self.fx.officers[0]))
        order.action_issue()
        self.authenticate('api_officer', 'Api-Officer-1')

        notices = self._json(self._call('GET', '/api/employee-portal/notifications'))['notifications']
        self.assertIn(order.id, [n['order_id'] for n in notices])

        transfers = self._json(self._call('GET', '/api/employee-portal/transfers'))['transfers']
        self.assertEqual(
            [(t['order_id'], t['order_number']) for t in transfers if t['order_id']],
            [(order.id, order.name)],
        )

        self.authenticate('api_other', 'Api-Other-1')
        other_notices = self._json(self._call('GET', '/api/employee-portal/notifications'))['notifications']
        self.assertNotIn(order.id, [n['order_id'] for n in other_notices])
