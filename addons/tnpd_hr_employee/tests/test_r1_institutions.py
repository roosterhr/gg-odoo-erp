# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Client round 1: the institution master (match the client's list, fixes), closed jails, prison overview."""

import json

from odoo.tests import HttpCase, TransactionCase, tagged

from .common import http_login, make_fixtures


@tagged('post_install', '-at_install')
class TestMasterReconcile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Master')
        cls.Jail = cls.env['prison.jail']
        cls.parent = cls.fx.src_central.name

    def _rows(self):
        return [
            {'name': 'R1Master Seyyurpatti', 'jail_type': 'sub_jail', 'parent': self.parent},
            {'name': 'R1Master Closedpatti', 'jail_type': 'sub_jail', 'parent': self.parent,
             'status': 'closed', 'remarks': 'Closed in 2024'},
        ]

    def _find(self, name):
        return self.Jail.with_context(active_test=False).search([('name', '=', name)])

    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        before = self.Jail.search_count([])
        result = self.Jail._reconcile_master(self._rows())
        self.assertFalse(result['applied'])
        self.assertEqual(result['summary']['add'], 2)
        self.assertEqual(result['after']['active_total'], result['before']['active_total'] + 2)
        self.assertEqual(self.Jail.search_count([]), before)
        self.assertFalse(self._find('R1Master Seyyurpatti'))

    def test_applying_adds_with_the_right_parent_and_status(self):
        result = self.Jail._reconcile_master(self._rows(), apply=True)
        self.assertTrue(result['applied'])
        added = self._find('R1Master Seyyurpatti')
        self.assertEqual(added.parent_id, self.fx.src_central)
        self.assertFalse(added.is_closed)
        closed = self._find('R1Master Closedpatti')
        self.assertTrue(closed.is_closed)
        self.assertEqual(closed.closed_remarks, 'Closed in 2024')

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self.Jail._reconcile_master(self._rows(), apply=True)
        again = self.Jail._reconcile_master(self._rows(), apply=True)
        self.assertEqual(again['summary'].get('unchanged'), 2)
        self.assertNotIn('add', again['summary'])

    def test_an_existing_one_is_renamed_moved_and_retagged(self):
        sub = self.fx.src_sub
        sub.write({'name': 'S.J. R1Master Oldname', 'is_closed': False})
        row = {'name': 'R1Master Oldname', 'jail_type': 'sub_jail', 'parent': self.fx.dst_central.name,
               'status': 'closed'}
        result = self.Jail._reconcile_master([row], apply=True)
        self.assertEqual(result['summary']['update'], 1)
        self.assertEqual((sub.name, sub.parent_id, sub.is_closed), ('R1Master Oldname', self.fx.dst_central, True))

    def test_a_duplicate_of_a_place_is_switched_off(self):
        twin = self.Jail.create({'name': 'S.J. R1Master Dup', 'jail_type': 'sub_jail', 'parent_id': self.fx.src_central.id})
        main = self.Jail.create({'name': 'R1Master Dup', 'jail_type': 'sub_jail', 'parent_id': self.fx.src_central.id})
        result = self.Jail._reconcile_master(
            [{'name': 'R1Master Dup', 'jail_type': 'sub_jail', 'parent': self.parent}], apply=True)
        self.assertEqual(result['summary'].get('deactivate'), 1)
        self.assertEqual((main.active, twin.active), (True, False))

    def test_a_bad_row_is_reported_and_the_others_still_go_through(self):
        rows = self._rows() + [{'name': 'R1Master Orphan', 'jail_type': 'sub_jail', 'parent': 'No Such Prison'},
                               {'name': 'R1Master Badtype', 'jail_type': 'castle'}]
        result = self.Jail._reconcile_master(rows, apply=True)
        self.assertEqual(result['summary']['errors'], 2)
        self.assertTrue(self._find('R1Master Seyyurpatti'))
        self.assertFalse(self._find('R1Master Orphan'))

    def test_ones_missing_from_the_list_can_be_switched_off(self):
        extra = self.Jail.create({'name': 'R1Master Extra', 'jail_type': 'sub_jail', 'parent_id': self.fx.src_central.id})
        self.Jail._reconcile_master(self._rows(), apply=True, deactivate_missing=True)
        self.assertFalse(extra.active)

    def test_the_totals_split_by_type_and_closed(self):
        totals = self.Jail._master_totals()
        self.assertEqual(totals['active_total'], sum(totals['by_type'].values()))
        self.assertEqual(totals['active_total'], totals['open'] + totals['closed'])


@tagged('post_install', '-at_install')
class TestKnownFixes(TransactionCase):

    def test_the_fixes_can_run_again_without_changing_anything(self):
        Jail = self.env['prison.jail']
        Jail._apply_known_fixes()
        self.assertEqual(Jail._apply_known_fixes(), [])

    def test_the_client_list_page_is_in_the_master_and_switched_on(self):
        Jail = self.env['prison.jail'].with_context(active_test=False)
        if not Jail._find_master('Thiruvarur', jail_types=['district_jail']):
            self.skipTest('no Thiruvarur district jail in this database')
        Jail._apply_known_fixes()
        thiruvarur = Jail._find_master('Thiruvarur', jail_types=['district_jail'])
        self.assertTrue(thiruvarur.active)
        for name in ('Thiruthuraipoondi', 'Nannilam', 'Mannarkudi'):
            sub = Jail._find_master(name, jail_types=['sub_jail'])
            self.assertEqual((sub.parent_id, sub.active), (thiruvarur, True), name)

    def test_a_near_spelling_is_the_same_place_not_a_new_one(self):
        fx = make_fixtures(self.env, 'R1Near')
        Jail = self.env['prison.jail']
        sub = Jail.create({'name': 'S.J. R1Near Mannargudi', 'jail_type': 'sub_jail', 'parent_id': fx.src_central.id})
        self.assertEqual(Jail._find_near('R1Near Mannarkudi', fx.src_central), sub)
        self.assertFalse(Jail._find_near('R1Near Sirkali', fx.src_central))

    def test_only_one_district_jail_stays_on_per_place(self):
        Jail = self.env['prison.jail'].with_context(active_test=False)
        main = Jail._find_master('Pudukkottai', jail_types=['district_jail'])
        if not main:
            self.skipTest('no Pudukkottai district jail in this database')
        central = Jail.search([('jail_type', '=', 'central_jail')], limit=1)
        Jail.create({'name': 'Pudukkottai District', 'jail_type': 'district_jail', 'parent_id': central.id})  # a duplicate of the place
        Jail._apply_known_fixes()
        live = Jail.search([('jail_type', '=', 'district_jail'), ('active', '=', True)]).filtered(
            lambda j: Jail._master_key(j.name) == 'pudukkottai')
        self.assertEqual(len(live), 1)

    def test_kodaikanal_is_temporarily_closed_under_madurai(self):
        Jail = self.env['prison.jail'].with_context(active_test=False)
        if not Jail._find_master('Kodaikanal', jail_types=['sub_jail']):
            self.skipTest('no Kodaikanal in this database')
        Jail._apply_known_fixes()
        kodai = Jail._find_master('Kodaikanal', jail_types=['sub_jail'])
        self.assertEqual(kodai.parent_id.name, 'Madurai')
        self.assertTrue(kodai.is_closed)

    def test_every_institution_is_tagged_active_or_closed(self):
        self.env.cr.execute("UPDATE prison_jail SET is_closed = NULL WHERE id = (SELECT MIN(id) FROM prison_jail)")
        self.env.invalidate_all()
        self.env['prison.jail']._apply_known_fixes()
        self.env.cr.execute("SELECT COUNT(*) FROM prison_jail WHERE is_closed IS NULL")
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_every_kodaikanal_record_is_under_madurai(self):
        Jail = self.env['prison.jail'].with_context(active_test=False)
        madurai = Jail._find_master('Madurai', jail_types=['central_jail'])
        if not madurai:
            self.skipTest('no Madurai central prison in this database')
        stray = Jail.create({'name': 'Kodaikanal (Closed)', 'jail_type': 'sub_jail', 'parent_id': madurai.id})
        other = Jail.search([('jail_type', '=', 'central_jail'), ('id', '!=', madurai.id)], limit=1)
        stray.parent_id = other
        Jail._apply_known_fixes()
        self.assertEqual(stray.parent_id, madurai)


@tagged('post_install', '-at_install')
class TestClosedJailsAndOverview(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Over')
        erp = cls.env.ref('base.group_erp_manager')
        cls.env['res.users'].create({
            'name': 'R1 admin', 'login': 'r1_over@test.local', 'password': 'r1_over@test.local',
            'group_ids': [(4, erp.id)], 'x_invited_by_admin': True})
        cls.env['res.users'].create({
            'name': 'R1 reg', 'login': 'r1_reg@test.local', 'password': 'r1_reg@test.local',
            'group_ids': [(4, erp.id)], 'x_invited_by_admin': True, 'x_access_scope': 'regional',
            'x_jurisdiction_jail_ids': [(6, 0, cls.fx.src_central.ids)]})
        cls.closed = cls.env['prison.jail'].create({
            'name': 'R1Over Closed Sub', 'jail_type': 'sub_jail', 'parent_id': cls.fx.src_central.id,
            'is_closed': True, 'closed_remarks': 'Closed for repairs'})
        # a closed jail's vacancy row is archived
        cls.env['prison.vacancy'].create({
            'prison_id': cls.closed.id, 'prison_name': cls.closed.name, 'prison_type': 'sub_jail',
            'sanctioned_strength': 10, 'occupied_count': 6, 'vacancy_count': 4, 'active': False})
        cls.fx.officers[0].x_sub_jail_id = cls.closed

    def _get(self, path, login='r1_over@test.local'):
        http_login(self, login)
        return self.url_open(path)

    def test_a_closed_jail_shows_in_the_hierarchy_with_its_vacancy_figures(self):
        data = self._get('/api/jails/hierarchy-with-vacancy?include_closed=true').json()
        parent = next(p for p in data['data'] if p['id'] == self.fx.src_central.id)
        child = next(c for c in parent['children'] if c['id'] == self.closed.id)
        self.assertTrue(child['is_closed'])
        self.assertEqual((child['sanctioned_strength'], child['vacancy_count']), (10, 4))

    def test_a_closed_jail_is_left_out_of_the_destination_list(self):
        data = self._get('/api/jails/hierarchy-with-vacancy').json()  # destinations ask without include_closed
        parent = next(p for p in data['data'] if p['id'] == self.fx.src_central.id)
        self.assertNotIn(self.closed.id, [c['id'] for c in parent['children']])

    def test_the_closed_jail_vacancy_by_designation_still_answers(self):
        data = self._get('/api/vacancy/designation?prison_id=%s' % self.closed.id).json()
        self.assertEqual(data['records'][0]['sanctioned_strength'], 10)

    def test_the_overview_shows_the_closed_jail_its_staff_and_vacancy(self):
        data = self._get('/api/jails/%s/overview' % self.closed.id).json()
        self.assertTrue(data['institution']['is_closed'])
        self.assertEqual(data['institution']['closed_remarks'], 'Closed for repairs')
        self.assertEqual(data['staff']['total'], 1)
        self.assertEqual(data['staff']['employees'][0]['name'], self.fx.officers[0].name)
        self.assertEqual(data['vacancy']['vacancy'], 4)

    def test_the_overview_of_a_prison_includes_the_jails_under_it(self):
        data = self._get('/api/jails/%s/overview' % self.fx.src_central.id).json()
        self.assertEqual(data['staff']['total'], 4)  # the three officers, the closed one among them, plus the other designation
        self.assertIn(self.closed.id, [c['id'] for c in data['institution']['children']])

    def test_the_overview_follows_the_callers_area(self):
        own = self._get('/api/jails/%s/overview' % self.fx.src_central.id, 'r1_reg@test.local').json()
        self.assertEqual(own['staff']['total'], 4)
        elsewhere = self._get('/api/jails/%s/overview' % self.fx.dst_central.id, 'r1_reg@test.local').json()
        self.assertEqual(elsewhere['staff']['total'], 0)

    def test_the_employee_list_can_be_filtered_to_a_closed_jail(self):
        data = self._get('/api/employees?jail_id=%s' % self.closed.id).json()
        self.assertEqual([e['name'] for e in data.get('employees', data.get('data', []))], [self.fx.officers[0].name])

    # ── the client's list ─────────────────────────────────────────────────────

    def _reconcile(self, body, login='r1_over@test.local'):
        http_login(self, login)
        return self.url_open('/api/jails/reconcile', data=json.dumps(body), headers={'Content-Type': 'application/json'})

    def test_the_csv_is_read_with_type_labels(self):
        csv_text = 'name,type,parent,status\nR1Over Csvpatti,Sub-Jail,%s,closed\n' % self.fx.src_central.name
        data = self._reconcile({'csv': csv_text}).json()
        self.assertEqual(data['summary']['add'], 1)
        self.assertFalse(data['applied'])
        self.assertFalse(self.env['prison.jail'].search([('name', '=', 'R1Over Csvpatti')]))
        applied = self._reconcile({'csv': csv_text, 'apply': True}).json()
        self.assertTrue(applied['applied'])
        self.assertTrue(self.env['prison.jail'].search([('name', '=', 'R1Over Csvpatti'), ('is_closed', '=', True)]))

    def test_a_regional_login_cannot_change_the_master(self):
        self.assertEqual(self._reconcile({'csv': 'name,type\nX,Sub-Jail'}, 'r1_reg@test.local').status_code, 403)

    def test_nothing_to_compare_is_an_error(self):
        self.assertEqual(self._reconcile({}).status_code, 400)
