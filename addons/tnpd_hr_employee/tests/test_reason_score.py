# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import json

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReasonScore(TransactionCase):
    """Priority score of a transfer request follows its reason (used for sorting only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Jail = cls.env['prison.jail']
        cls.central = Jail.create({'name': 'Score Central', 'jail_type': 'central_jail'})
        cls.sub = Jail.create({
            'name': 'Score Sub', 'jail_type': 'sub_jail', 'parent_id': cls.central.id,
        })
        cls.Request = cls.env['transfer.approval.request']

    def _request(self, reason, n):
        employee = self.env['hr.employee'].create({
            'name': 'Score Officer %s' % n, 'x_employee_code': 'SCORE-%s' % n,
        })
        return self.Request.create({
            'employee_id': employee.id,
            'transfer_type': 'request',
            'reason_category': reason,
            'requested_central_prison': self.central.id,
            'requested_sub_jail': self.sub.id,
            'approval_user_id': self.env.user.id,
            'requested_by': self.env.user.id,
            'state': 'draft',
        })

    def test_agreed_scores(self):
        self.assertEqual(self.Request.REASON_SCORES, {
            'illness_self': 8, 'retirement_one_year': 7, 'spouse_working': 6,
            'single_parent': 5, 'ex_serviceman': 4, 'illness_family': 3,
            'long_service_outside_range': 2, 'mutual_request': 1,
            'medical_reasons': 8,  # older requests only
            'others': 0,
        })

    def test_every_reason_in_the_list_has_a_score(self):
        listed = {value for value, _label in self.Request._fields['reason_category'].selection}
        self.assertEqual(listed, set(self.Request.REASON_SCORES))

    def test_score_follows_the_reason(self):
        req = self._request('single_parent', 1)
        self.assertEqual(req.reason_score, 5)
        req.reason_category = 'illness_self'
        self.assertEqual(req.reason_score, 8)

    def test_no_reason_scores_zero(self):
        self.assertEqual(self._request(False, 2).reason_score, 0)

    def test_highest_score_first_then_oldest_first(self):
        mutual = self._request('mutual_request', 3)
        first_ill = self._request('illness_self', 4)
        retire = self._request('retirement_one_year', 5)
        second_ill = self._request('illness_self', 6)
        ordered = self.Request.search(
            [('id', 'in', (mutual + first_ill + retire + second_ill).ids)],
            order='reason_score desc, id asc',
        )
        self.assertEqual(ordered, first_ill + second_ill + retire + mutual)

    def test_retired_reason_is_refused_on_new_requests(self):
        with self.assertRaises(ValidationError):
            self._request('medical_reasons', 7)
        req = self._request('others', 8)
        with self.assertRaises(ValidationError):
            req.reason_category = 'medical_reasons'

    def test_existing_request_keeps_its_retired_reason(self):
        req = self._request('others', 9)
        # An older request, as it exists in the database from before the change.
        self.env.cr.execute(
            "UPDATE transfer_approval_request SET reason_category = 'medical_reasons' WHERE id = %s",
            (req.id,),
        )
        req.invalidate_recordset()
        req.write({'remarks': 'still editable'})
        req.write({'reason_category': 'medical_reasons'})  # unchanged value is fine
        self.assertEqual(req.remarks, 'still editable')
        self.assertEqual(req.reason_category, 'medical_reasons')


@tagged('post_install', '-at_install')
class TestListAllSort(HttpCase):
    """GET /api/transfer/list-all: sort=priority and offset."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Jail = cls.env['prison.jail']
        central = Jail.create({'name': 'Sort Central', 'jail_type': 'central_jail'})
        sub = Jail.create({'name': 'Sort Sub', 'jail_type': 'sub_jail', 'parent_id': central.id})
        cls.user = cls.env['res.users'].create({
            'name': 'Sort Tester', 'login': 'sort_tester', 'password': 'Sort-Tester-1',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        for n, reason in enumerate(
            ['mutual_request', 'illness_self', 'retirement_one_year', 'illness_self', 'others']
        ):
            employee = cls.env['hr.employee'].create({
                'name': 'Sort Officer %s' % n, 'x_employee_code': 'SORT-%s' % n,
            })
            cls.env['transfer.approval.request'].create({
                'employee_id': employee.id, 'transfer_type': 'request',
                'reason_category': reason, 'requested_central_prison': central.id,
                'requested_sub_jail': sub.id, 'approval_user_id': cls.user.id,
                'requested_by': cls.user.id, 'state': 'draft',
            })

    def _list(self, **params):
        query = '&'.join('%s=%s' % kv for kv in params.items())
        response = self.url_open('/api/transfer/list-all?' + query)
        self.assertEqual(response.status_code, 200)
        return response.json()['records']

    def _id(self, record):
        return record.get('request_id') or record.get('id')

    def test_priority_sort_is_highest_score_first_oldest_first(self):
        self.authenticate('sort_tester', 'Sort-Tester-1')
        records = self._list(limit=100, sort='priority')
        scores = [r['reason_score'] for r in records]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for earlier, later in zip(records, records[1:]):
            if earlier['reason_score'] == later['reason_score']:
                self.assertLess(self._id(earlier), self._id(later))

    def test_default_order_is_still_newest_first(self):
        self.authenticate('sort_tester', 'Sort-Tester-1')
        ids = [self._id(r) for r in self._list(limit=100)]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_offset_continues_the_same_order(self):
        self.authenticate('sort_tester', 'Sort-Tester-1')
        full = [self._id(r) for r in self._list(limit=100, sort='priority')]
        first = [self._id(r) for r in self._list(limit=2, offset=0, sort='priority')]
        second = [self._id(r) for r in self._list(limit=2, offset=2, sort='priority')]
        self.assertEqual(first + second, full[:4])

    def test_reason_filter_returns_only_that_reason(self):
        self.authenticate('sort_tester', 'Sort-Tester-1')
        records = self._list(limit=100, reason='illness_self')
        self.assertGreaterEqual(len(records), 2)  # the two created in setUpClass
        self.assertEqual({r['reason_category'] for r in records}, {'illness_self'})
        self.assertEqual({r['reason_score'] for r in records}, {8})

    def test_unknown_reason_filter_is_ignored(self):
        self.authenticate('sort_tester', 'Sort-Tester-1')
        everything = [self._id(r) for r in self._list(limit=100)]
        self.assertEqual([self._id(r) for r in self._list(limit=100, reason='nope')], everything)


@tagged('post_install', '-at_install')
class TestCreateDraft(HttpCase):
    """POST /api/transfer/create-draft: the reason and priority it accepts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Jail = cls.env['prison.jail']
        cls.central = Jail.create({'name': 'Draft Central', 'jail_type': 'central_jail'})
        cls.sub = Jail.create({
            'name': 'Draft Sub', 'jail_type': 'sub_jail', 'parent_id': cls.central.id,
        })
        cls.user = cls.env['res.users'].create({
            'name': 'Draft Tester', 'login': 'draft_tester', 'password': 'Draft-Tester-1',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id, cls.env.ref('hr.group_hr_user').id,
            ])],
        })

    def _employee(self, n):
        return self.env['hr.employee'].create({
            'name': 'Draft Officer %s' % n, 'x_employee_code': 'DRAFT-%s' % n,
        })

    def _post(self, employee, **extra):
        body = dict({
            'employee_id': employee.id,
            'requested_central_prison': self.central.id,
            'requested_sub_jail': self.sub.id,
        }, **extra)
        self.authenticate('draft_tester', 'Draft-Tester-1')
        response = self.url_open(
            '/api/transfer/create-draft', data=json.dumps(body),
            headers={'Content-Type': 'application/json'},
        )
        return response.json()

    def _requests_of(self, employee):
        return self.env['transfer.approval.request'].search([('employee_id', '=', employee.id)])

    def test_valid_reason_creates_a_draft_with_its_score(self):
        employee = self._employee(1)
        result = self._post(employee, reason_category='single_parent', priority='high')
        self.assertTrue(result['success'], result)
        request = self._requests_of(employee)
        self.assertEqual((request.reason_category, request.reason_score), ('single_parent', 5))
        self.assertEqual(request.priority, 'high')

    def test_reason_is_optional(self):
        employee = self._employee(2)
        result = self._post(employee)
        self.assertTrue(result['success'], result)
        request = self._requests_of(employee)
        self.assertFalse(request.reason_category)
        self.assertEqual((request.reason_score, request.priority), (0, 'medium'))

    def test_retired_and_unknown_reasons_are_refused(self):
        employee = self._employee(3)
        for reason in ('medical_reasons', 'nope'):
            result = self._post(employee, reason_category=reason)
            self.assertFalse(result['success'])
            self.assertEqual(result['message'], 'Invalid reason_category')
        self.assertFalse(self._requests_of(employee))

    def test_unknown_priority_is_refused_cleanly(self):
        employee = self._employee(4)
        result = self._post(employee, reason_category='single_parent', priority='urgent')
        self.assertFalse(result['success'])
        self.assertEqual(result['message'], 'Invalid priority')
        self.assertFalse(self._requests_of(employee))
