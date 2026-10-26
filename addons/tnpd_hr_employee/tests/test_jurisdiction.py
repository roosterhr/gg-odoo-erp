# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.tnpd_scope_mixin import SCOPE_CTX
from ..scope import scoped_jails
from .common import approved_transfer, make_fixtures


@tagged('post_install', '-at_install')
class TestJurisdiction(TransactionCase):
    """Headquarters sees everything; a Regional user only their institutions and what is under them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Scope')
        Users = cls.env['res.users']
        cls.hq = Users.create({'name': 'Scope HQ', 'login': 'scope_hq_t@test.local'})
        cls.regional = Users.create({
            'name': 'Scope Regional', 'login': 'scope_reg_t@test.local',
            'x_access_scope': 'regional',
            'x_jurisdiction_jail_ids': [(6, 0, [cls.fx.src_central.id])],
        })
        cls.nobody = Users.create({
            'name': 'Scope Nobody', 'login': 'scope_none_t@test.local', 'x_access_scope': 'regional',
        })
        # one officer who already sits in the other region
        cls.far_officer = cls.env['hr.employee'].create({
            'name': 'Scope Far Officer', 'x_employee_code': 'SCOPE-0099',
            'x_central_jail_id': cls.fx.dst_central.id, 'x_sub_jail_id': cls.fx.dst_sub.id,
        })
        cls.transfer = approved_transfer(cls.env, cls.fx, cls.fx.officers[0])
        cls.far_transfer = approved_transfer(cls.env, cls.fx, cls.far_officer)
        cls.far_transfer.write({'current_central_prison': cls.fx.dst_central.id,
                                'current_sub_jail': cls.fx.dst_sub.id})

    def _as(self, user, model):
        return self.env[model].sudo().with_context(**{SCOPE_CTX: user.id})

    def test_headquarters_is_not_limited(self):
        self.assertIsNone(self.hq._tnpd_scope_jail_ids())
        self.assertIn(self.far_officer, self._as(self.hq, 'hr.employee').search([]))

    def test_odoo_system_admin_is_headquarters_even_if_marked_regional(self):
        admin = self.env.ref('base.user_admin')
        admin.x_access_scope = 'regional'
        self.assertIsNone(admin._tnpd_scope_jail_ids())

    def test_regional_sees_their_prison_and_what_is_under_it(self):
        ids = self.regional._tnpd_scope_jail_ids()
        self.assertEqual(set(ids), {self.fx.src_central.id, self.fx.src_sub.id})
        employees = self._as(self.regional, 'hr.employee').search([])
        self.assertTrue(set(self.fx.officers) <= set(employees))
        self.assertNotIn(self.far_officer, employees)

    def test_regional_without_a_prison_sees_nothing(self):
        self.assertEqual(self._as(self.nobody, 'hr.employee').search_count([]), 0)

    def test_transfers_follow_the_officer_area(self):
        transfers = self._as(self.regional, 'transfer.approval.request').search([])
        self.assertIn(self.transfer, transfers)
        self.assertNotIn(self.far_transfer, transfers)

    def test_orders_follow_their_transfers(self):
        mine = self.env['transfer.order'].create({'transfer_ids': [(6, 0, self.transfer.ids)]})
        far = self.env['transfer.order'].create({'transfer_ids': [(6, 0, self.far_transfer.ids)]})
        orders = self._as(self.regional, 'transfer.order').search([])
        self.assertIn(mine, orders)
        self.assertNotIn(far, orders)

    def test_counts_and_groups_are_limited_too(self):
        employees = self._as(self.regional, 'hr.employee')
        self.assertEqual(employees.search_count([]), len(employees.search([])))
        grouped = employees._read_group([], [], ['__count'])
        self.assertEqual(sum(row[0] for row in grouped), employees.search_count([]))

    def test_related_records_outside_the_area_still_load(self):
        """A transfer in the area points at a destination jail outside it; reading that must work."""
        transfer = self._as(self.regional, 'transfer.approval.request').search([('id', '=', self.transfer.id)])
        self.assertEqual(transfer.requested_central_prison.name, self.fx.dst_central.name)

    def test_a_sub_jail_login_sees_only_its_own_officers(self):
        sub_user = self.env['res.users'].create({
            'name': 'Scope Sub', 'login': 'scope_sub_t@test.local', 'x_access_scope': 'regional',
            'x_jurisdiction_jail_ids': [(6, 0, [self.fx.src_sub.id])],
        })
        self.assertEqual(sub_user._tnpd_scope_jail_ids(), [self.fx.src_sub.id])
        self.assertEqual(self._as(sub_user, 'hr.employee').search_count([]), len(self.fx.officers) + 1)

    def test_institutions_are_the_same_for_everyone(self):
        jails = self._as(self.regional, 'prison.jail').search([])
        self.assertIn(self.fx.dst_central, jails)
        self.assertEqual(len(jails), self.env['prison.jail'].sudo().search_count([]))

    def test_only_the_dashboard_limits_institutions(self):
        limited = self._as(self.regional, 'prison.jail').with_context(tnpd_scope_jails=True).search([])
        self.assertEqual(set(limited.ids), {self.fx.src_central.id, self.fx.src_sub.id})

    def test_dashboard_institution_lists_follow_the_area(self):
        """Only the dashboard asks for institutions limited to the area."""
        jails = scoped_jails(self.env, self.regional.id).search([])
        self.assertEqual(set(jails.ids), {self.fx.src_central.id, self.fx.src_sub.id})
        self.assertEqual(scoped_jails(self.env, self.hq.id).search_count([]),
                         self.env['prison.jail'].sudo().search_count([]))

    def test_a_search_by_id_is_still_limited(self):
        found = self._as(self.regional, 'hr.employee').search([('id', '=', self.far_officer.id)])
        self.assertFalse(found)


@tagged('post_install', '-at_install')
class TestJurisdictionHttp(HttpCase):
    """The net under every /api/ route: a Regional session reads its area and cannot change anything."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fx = make_fixtures(cls.env, 'ScopeHttp')
        erp = cls.env.ref('base.group_erp_manager')
        for login, scope, jails in (('http_hq@test.local', 'hq', []),
                                    ('http_reg@test.local', 'regional', fx.src_central.ids)):
            cls.env['res.users'].create({
                'name': login, 'login': login, 'password': login, 'x_access_scope': scope,
                'x_jurisdiction_jail_ids': [(6, 0, jails)], 'group_ids': [(4, erp.id)],
            })
        cls.fx = fx

    def _post(self, path):
        return self.url_open(path, data='{}', headers={'Content-Type': 'application/json'})

    def test_regional_cannot_write_anywhere(self):
        self.authenticate('http_reg@test.local', 'http_reg@test.local')
        for path in ('/api/vacancy/update', '/api/employees', '/api/transfer/orders/create'):
            self.assertEqual(self._post(path).status_code, 403, path)

    def test_regional_may_still_read(self):
        self.authenticate('http_reg@test.local', 'http_reg@test.local')
        self.assertEqual(self.url_open('/api/employees?limit=1').status_code, 200)

    def test_headquarters_is_not_blocked(self):
        self.authenticate('http_hq@test.local', 'http_hq@test.local')
        self.assertNotEqual(self._post('/api/vacancy/update').status_code, 403)

    def test_analytics_and_vacancy_are_the_same_for_everyone(self):
        seen = {}
        for who in ('http_reg@test.local', 'http_hq@test.local'):
            self.authenticate(who, who)
            seen[who] = [self.url_open(path).json() for path in (
                '/api/analytics/rank-distribution', '/api/analytics/institution-headcount',
                '/api/analytics/retirement-forecast', '/api/analytics/vacancy-trend',
                '/api/vacancy/dashboard',
            )]
        self.assertEqual(seen['http_reg@test.local'], seen['http_hq@test.local'])

    def test_regional_sees_all_prisons_in_the_lists_like_headquarters(self):
        names = {}
        for who in ('http_reg@test.local', 'http_hq@test.local'):
            self.authenticate(who, who)
            names[who] = [p['name'] for p in self.url_open('/api/jails/hierarchy-with-vacancy').json().get('data', [])]
        self.assertEqual(names['http_reg@test.local'], names['http_hq@test.local'])
        self.assertIn(self.fx.dst_central.name, names['http_reg@test.local'])
