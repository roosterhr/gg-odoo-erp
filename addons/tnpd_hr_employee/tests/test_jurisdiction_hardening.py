# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import json

from werkzeug.exceptions import Forbidden

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from ..models.tnpd_scope_mixin import SCOPE_CTX
from ..scope import scoped
from .common import approved_transfer, http_login, make_fixtures


@tagged('post_install', '-at_install')
class TestJurisdictionHardening(HttpCase):
    """The other doors: Odoo's own web client and RPC, the scope fields, and sign-in edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'ScopeHard')
        erp = cls.env.ref('base.group_erp_manager')
        Users = cls.env['res.users']
        cls.hq = Users.create({'name': 'hard_hq', 'login': 'hard_hq@test.local', 'password': 'hard_hq@test.local',
                               'group_ids': [(4, erp.id)], 'x_invited_by_admin': True})
        cls.reg = Users.create({
            'name': 'hard_reg', 'login': 'hard_reg@test.local', 'password': 'hard_reg@test.local',
            'x_access_scope': 'regional', 'x_jurisdiction_jail_ids': [(6, 0, cls.fx.src_central.ids)],
            'group_ids': [(4, erp.id)], 'x_invited_by_admin': True,
        })
        cls.far = cls.env['hr.employee'].create({
            'name': 'Hard Far', 'x_employee_code': 'HARD-0099',
            'x_central_jail_id': cls.fx.dst_central.id, 'x_sub_jail_id': cls.fx.dst_sub.id,
        })

    def _json_post(self, path, payload):
        return self.url_open(path, data=json.dumps(payload), headers={'Content-Type': 'application/json'})

    # ── Odoo's web client and RPC ─────────────────────────────────────────────

    def test_regional_cannot_use_the_odoo_web_client(self):
        http_login(self, 'hard_reg@test.local', 'hard_reg@test.local')
        call = {'jsonrpc': '2.0', 'params': {'model': 'hr.employee', 'method': 'search_read', 'args': [], 'kwargs': {}}}
        reply = self._json_post('/web/dataset/call_kw/hr.employee/search_read', call).json()  # JSON-RPC: HTTP 200
        self.assertEqual(reply['error']['data']['name'], 'werkzeug.exceptions.Forbidden')
        self.assertEqual(self.url_open('/odoo', allow_redirects=False).status_code, 403)

    def test_regional_cannot_sign_in_to_odoo_rpc(self):
        for login, may in (('hard_reg@test.local', False), ('hard_hq@test.local', True)):
            reply = self._json_post('/jsonrpc', {'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': {
                'service': 'common', 'method': 'login', 'args': [self.env.cr.dbname, login, login]}}).json()
            self.assertEqual(bool(reply.get('result')), may, login)

    def test_regional_still_signs_in_through_the_app(self):
        reply = self._json_post('/web/session/authenticate', {'jsonrpc': '2.0', 'params': {
            'db': self.env.cr.dbname, 'login': 'hard_reg@test.local', 'password': 'hard_reg@test.local'}}).json()
        self.assertTrue(reply.get('result', {}).get('uid'))

    # ── The scope fields ──────────────────────────────────────────────────────

    def test_a_regional_login_cannot_change_its_own_scope(self):
        with self.assertRaises(AccessError):
            self.reg.with_user(self.reg).write({'x_access_scope': 'hq'})
        with self.assertRaises(AccessError):
            self.reg.with_user(self.reg).write({'x_jurisdiction_jail_ids': [(6, 0, self.fx.dst_central.ids)]})

    def test_headquarters_can_change_a_scope(self):
        self.reg.with_user(self.hq).write({'x_jurisdiction_jail_ids': [(6, 0, self.fx.dst_central.ids)]})
        self.assertEqual(self.reg.x_jurisdiction_jail_ids, self.fx.dst_central)

    def _put_user(self, user_id, body):
        return self.url_open('/api/users/%s' % user_id, data=json.dumps(body), method='PUT',
                             headers={'Content-Type': 'application/json'})

    def test_headquarters_cannot_take_away_its_own_access(self):
        http_login(self, 'hard_hq@test.local', 'hard_hq@test.local')
        reply = self._put_user(self.hq.id, {'name': 'x', 'access_scope': 'regional',
                                            'jurisdiction_jail_ids': self.fx.src_central.ids})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.hq.x_access_scope, 'hq')

    def test_update_user_validates_the_scope(self):
        http_login(self, 'hard_hq@test.local', 'hard_hq@test.local')
        for body in ({'name': 'x', 'access_scope': 'bogus'},
                     {'name': 'x', 'access_scope': 'regional', 'jurisdiction_jail_ids': []},
                     {'name': 'x', 'access_scope': 'regional', 'jurisdiction_jail_ids': [999999999]},
                     {'name': 'x', 'access_scope': 'regional', 'jurisdiction_jail_ids': ['a']}):
            self.assertEqual(self._put_user(self.reg.id, body).status_code, 400, body)
        self.assertEqual(self.reg.x_jurisdiction_jail_ids, self.fx.src_central)

    # ── Sign-in edge cases ────────────────────────────────────────────────────

    def test_an_unauthenticated_write_is_a_401_not_a_500(self):
        self.assertEqual(self._json_post('/api/transfer/orders/create', {'request_ids': [1]}).status_code, 401)

    def test_a_regional_login_may_still_change_its_own_password(self):
        http_login(self, 'hard_reg@test.local', 'hard_reg@test.local')
        self.assertNotEqual(self._json_post('/api/change-password', {}).status_code, 403)

    # ── The area filter ───────────────────────────────────────────────────────

    def test_a_search_by_id_written_in_an_endpoint_is_still_limited(self):
        employees = self.env['hr.employee'].sudo().with_context(**{SCOPE_CTX: self.reg.id})
        self.assertFalse(employees.search([('id', 'in', [self.far.id])]))
        self.assertFalse(employees.search([('id', 'in', [self.far.id])], limit=1))

    def test_odoo_loading_a_related_record_outside_the_area_still_works(self):
        transfer = approved_transfer(self.env, self.fx, self.fx.officers[0])
        transfer = self.env['transfer.approval.request'].sudo().with_context(**{SCOPE_CTX: self.reg.id}) \
            .search([('id', '=', transfer.id)])
        self.assertEqual(transfer.requested_central_prison.name, self.fx.dst_central.name)

    def test_with_no_signed_in_user_nothing_is_returned_as_if_it_were_everything(self):
        with self.assertRaises(Forbidden):
            scoped(self.env, 'hr.employee')

    # ── New endpoints cannot silently miss the area ───────────────────────────

    # GET routes that answer the same for a Regional login as for Headquarters on purpose: institutions,
    # vacancy and analytics are the same for everyone; the rest hold nothing that depends on the area.
    SAME_FOR_EVERYONE = {
        '/api/analytics/institution-headcount', '/api/analytics/rank-distribution',
        '/api/analytics/retirement-forecast', '/api/analytics/summary',
        '/api/analytics/transfer-activity-by-district', '/api/analytics/transfers-by-month',
        '/api/analytics/vacancy-trend',
        '/api/jails/central', '/api/jails/closed', '/api/jails/export', '/api/jails/filter-list',
        '/api/jails/hierarchy', '/api/jails/hierarchy-with-vacancy',
        '/api/transfer/prison-master', '/api/transfer/vacancy-summary',
        '/api/vacancy/dashboard', '/api/vacancy/roles',
        '/api/stats',                                  # public
        '/api/employees/import-template',              # a static file
        '/api/dashboard/jail-categories',              # a fixed list of category names
        '/api/transfer/fetch-approval-requests',       # the caller's own inbox (empty for both)
        '/api/auth/me',                                # answers 500 for both in the test cursor (CREATE TABLE)
    }

    def test_every_get_route_is_limited_or_listed_as_the_same_for_everyone(self):
        routes = sorted({
            rule.rule for rule in self.env['ir.http'].routing_map().iter_rules()
            if rule.rule.startswith('/api/') and 'GET' in (rule.methods or ()) and '<' not in rule.rule
        })
        answers = {}
        for who in ('hard_reg@test.local', 'hard_hq@test.local'):
            http_login(self, who, who)
            answers[who] = {path: (r.status_code, r.text) for path in routes for r in [self.url_open(path)]}
        # Only answers that carry data can show a missing limit: routes that need a parameter or
        # a portal sign-in answer an error for both.
        same = {p for p in routes
                if answers['hard_reg@test.local'][p] == answers['hard_hq@test.local'][p]
                and answers['hard_reg@test.local'][p][0] == 200}
        self.assertFalse(same - self.SAME_FOR_EVERYONE,
                         'GET routes that answer the same for a Regional login and for Headquarters. Limit them '
                         'to the area, or list them in SAME_FOR_EVERYONE if that is intended: %s'
                         % sorted(same - self.SAME_FOR_EVERYONE))
