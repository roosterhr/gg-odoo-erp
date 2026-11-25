# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Every posting has the same 3-year tenure threshold: there is no hill station rule."""

from datetime import date, timedelta

from odoo.tests import HttpCase, tagged

from .common import http_login, make_fixtures


@tagged('post_install', '-at_install')
class TestOneTenureThreshold(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Tenure')
        cls.env['res.users'].create({
            'name': 'R1 tenure admin', 'login': 'r1_tenure@test.local', 'password': 'r1_tenure@test.local',
            'group_ids': [(4, cls.env.ref('base.group_erp_manager').id)], 'x_invited_by_admin': True})
        cls.short, cls.long = cls.fx.officers[0], cls.fx.officers[1]
        cls.short.x_date_present_station = date.today() - timedelta(days=int(2.5 * 365.25))  # over 18 months
        cls.long.x_date_present_station = date.today() - timedelta(days=int(3.5 * 365.25))

    def _get(self, url):
        http_login(self, 'r1_tenure@test.local')
        return self.url_open(url).json()

    def test_there_is_no_hill_station_field_on_a_jail(self):
        self.assertNotIn('is_hill_station', self.env['prison.jail']._fields)

    def test_only_officers_with_three_years_are_eligible_for_a_tenure_transfer(self):
        data = self._get('/api/transfer/eligible-tenure?limit=100&search=R1Tenure')
        codes = [e['employee_code'] for e in data['employees']]
        self.assertIn(self.long.x_employee_code, codes)
        self.assertNotIn(self.short.x_employee_code, codes)  # 2.5 years was enough under the old 18-month rule
        self.assertTrue(all('is_hill_station' not in e for e in data['employees']))

    def test_the_hill_filter_is_ignored(self):
        plain = self._get('/api/transfer/eligible-tenure?limit=100&search=R1Tenure')
        filtered = self._get('/api/transfer/eligible-tenure?limit=100&search=R1Tenure&is_hill_station=true')
        self.assertEqual([e['employee_code'] for e in plain['employees']],
                         [e['employee_code'] for e in filtered['employees']])

    def test_overstay_occupants_use_three_years_and_say_so(self):
        data = self._get('/api/transfer/overstay-occupants?prison_id=%s' % self.fx.src_sub.id)
        self.assertEqual(data['threshold_label'], '3 years')
        self.assertNotIn('is_hill_station', data)
        names = [r['name'] for r in data['records']]
        self.assertIn(self.long.name, names)
        self.assertNotIn(self.short.name, names)
