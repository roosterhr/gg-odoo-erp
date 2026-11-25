# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Dashboard: the Special Prisons for Women are the Women's Prisons category."""

from odoo.tests import HttpCase, tagged

from .common import http_login, make_fixtures


@tagged('post_install', '-at_install')
class TestDashboardWomenPrisons(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Dash')
        cls.women_prison = cls.env['prison.jail'].create(
            {'name': 'R1Dash Women Prison', 'jail_type': 'spw', 'hierarchy_type': 'women'})
        cls.officer = cls.fx.officers[0]
        cls.officer.write({'x_central_jail_id': cls.women_prison.id, 'x_sub_jail_id': False})
        cls.env['res.users'].create({
            'name': 'R1 dash admin', 'login': 'r1_dash@test.local', 'password': 'r1_dash@test.local',
            'group_ids': [(4, cls.env.ref('base.group_erp_manager').id)], 'x_invited_by_admin': True})

    def _get(self, url):
        http_login(self, 'r1_dash@test.local')
        return self.url_open(url).json()

    def _category(self, data, category_id):
        return next(c for c in data['categories'] if c['categoryId'] == category_id)

    def test_the_women_prisons_are_counted_by_their_type(self):
        Jail = self.env['prison.jail']
        data = self._get('/api/dashboard/jail-category-personnel')
        self.assertEqual(self._category(data, 'women_prison')['prisonCount'],
                         Jail.search_count([('jail_type', '=', 'spw'), ('active', '=', True)]))
        self.assertEqual(self._category(data, 'central_prison')['prisonCount'],
                         Jail.search_count([('jail_type', '=', 'central_jail'), ('active', '=', True)]))
        self.assertGreaterEqual(self._category(data, 'women_prison')['prisonCount'], 1)

    def test_an_officer_posted_at_a_women_prison_counts_under_women_prisons(self):
        data = self._get('/api/dashboard/jail-category-personnel?institutionId=%s' % self.women_prison.id)
        self.assertEqual(self._category(data, 'women_prison')['officerCount'], 1)
        self.assertEqual(self._category(data, 'central_prison')['officerCount'], 0)

    def test_the_women_prison_list_has_them_and_the_central_list_does_not(self):
        women = self._get('/api/dashboard/institutions?categoryId=women_prison')['institutions']
        central = self._get('/api/dashboard/institutions?categoryId=central_prison')['institutions']
        self.assertIn(self.women_prison.id, [j['id'] for j in women])
        self.assertNotIn(self.women_prison.id, [j['id'] for j in central])
        self.assertIn(self.fx.src_central.id, [j['id'] for j in central])
