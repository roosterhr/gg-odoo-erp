# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo.tests import TransactionCase, tagged


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
