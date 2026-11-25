# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Employee portal: the three preferences of a transfer request, including a Central Prison on its own."""

import json

from odoo.tests import HttpCase, tagged

from .common import http_login, make_fixtures


@tagged('post_install', '-at_install')
class TestCentralPrisonPreference(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Pref')
        Jail = cls.env['prison.jail']
        cls.third_central = Jail.create({'name': 'R1Pref Third Central', 'jail_type': 'central_jail'})
        cls.women_prison = Jail.create({'name': 'R1Pref Women Prison', 'jail_type': 'spw', 'hierarchy_type': 'women'})
        portal = cls.env.ref('base.group_portal')
        cls.officer = cls.fx.officers[0]
        cls.officer.user_id = cls.env['res.users'].create({
            'name': cls.officer.name, 'login': cls.officer.x_employee_code,
            'password': cls.officer.x_employee_code, 'group_ids': [(6, 0, [portal.id])]})

    def _submit(self, p1, p2, p3):
        """Each preference is (central id, sub-jail id or None)."""
        http_login(self, self.officer.x_employee_code)
        body = {
            'reason_categories': ['mutual_request'],
            'to_central_jail_id': p1[0], 'to_sub_jail_id': p1[1],
            'p2_central_jail_id': p2[0], 'p2_sub_jail_id': p2[1],
            'p3_central_jail_id': p3[0], 'p3_sub_jail_id': p3[1],
        }
        return self.url_open('/api/employee-portal/transfers', data=json.dumps(body),
                             headers={'Content-Type': 'application/json'}).json()

    def test_a_central_prison_can_be_a_preference_on_its_own(self):
        fx = self.fx
        reply = self._submit((fx.dst_central.id, None), (fx.dst_central.id, fx.dst_sub.id), (self.third_central.id, None))
        self.assertTrue(reply['success'], reply)
        request = self.env['transfer.approval.request'].browse(reply['request_id'])
        self.assertEqual(request.requested_central_prison, fx.dst_central)
        self.assertFalse(request.requested_sub_jail)
        self.assertEqual(request.preference_2_sub_jail, fx.dst_sub)  # a sub jail under that same prison is a different place
        self.assertEqual(request.preference_3_central_prison, self.third_central)
        self.assertFalse(request.preference_3_sub_jail)

    def test_the_same_central_prison_twice_is_refused(self):
        fx = self.fx
        reply = self._submit((fx.dst_central.id, None), (fx.dst_central.id, None), (self.third_central.id, None))
        self.assertFalse(reply['success'])
        self.assertIn('different', reply['message'])

    def test_a_preference_still_needs_its_central_prison(self):
        fx = self.fx
        reply = self._submit((fx.dst_central.id, None), (None, fx.dst_sub.id), (self.third_central.id, None))
        self.assertFalse(reply['success'])

    def test_a_women_prison_is_refused_to_an_officer_of_the_general_side(self):
        fx = self.fx
        reply = self._submit((fx.dst_central.id, None), (self.women_prison.id, None), (self.third_central.id, None))
        self.assertFalse(reply['success'])
        self.assertIn('hierarchy', reply['message'])
