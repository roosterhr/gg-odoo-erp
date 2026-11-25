# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Client round 1: the institution master (match the client's list, fixes), closed jails, prison overview."""


from odoo.exceptions import UserError
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

    def test_merge_from_moves_the_officers_and_switches_the_old_record_off(self):
        old = self.fx.src_sub
        officers = self.fx.officers
        row = {'name': 'R1Master Keeper', 'jail_type': 'sub_jail', 'parent': self.parent,
               'merge_from': [{'name': old.name, 'type': 'sub_jail'}]}
        result = self.Jail._reconcile_master([row], apply=True)
        keeper = self._find('R1Master Keeper')
        self.assertEqual(result['summary'].get('merge'), 1)
        self.assertFalse(old.active)
        self.assertEqual([o.x_sub_jail_id for o in officers], [keeper] * len(officers))

    def test_the_children_of_a_folded_record_follow_it_to_the_kept_one(self):
        old = self.Jail.create({'name': 'R1Master OldDJ', 'jail_type': 'district_jail', 'parent_id': self.fx.src_central.id})
        child = self.Jail.create({'name': 'R1Master Underpatti', 'jail_type': 'sub_jail', 'parent_id': old.id})
        self.assertEqual(child.central_jail_id, old)
        row = {'name': 'R1Master KeeperDJ', 'jail_type': 'district_jail', 'parent': self.parent,
               'merge_from': [{'name': old.name, 'type': 'district_jail'}]}
        self.Jail._reconcile_master([row], apply=True)
        keeper = self._find('R1Master KeeperDJ')
        self.assertEqual(child.parent_id, keeper)
        self.assertEqual(child.central_jail_id, keeper)  # the stored link followed, not left on the old record
        self.assertFalse(old.active)

    def test_a_transfer_to_a_switched_off_institution_cannot_be_approved(self):
        from .common import approved_transfer
        transfer = approved_transfer(self.env, self.fx, self.fx.officers[0])
        transfer.write({'state': 'pending'})
        self.fx.dst_sub.active = False
        with self.assertRaises(UserError):
            transfer._validate_vacancy_and_gender()

    def test_an_institution_with_an_empty_tree_path_does_not_corrupt_the_others(self):
        mover, bystander = self.fx.src_sub, self.fx.dst_sub
        self.env.cr.execute('UPDATE prison_jail SET parent_path = NULL WHERE id = %s', (mover.id,))
        mover.invalidate_recordset()
        row = {'name': mover.name, 'jail_type': 'sub_jail', 'parent': self.fx.dst_central.name}
        self.Jail._reconcile_master([row], apply=True)
        for jail in (mover, bystander):
            self.assertTrue(jail.parent_path.endswith('/%s/' % jail.id), jail.parent_path)
        self.assertEqual(mover.parent_id, self.fx.dst_central)

    def test_a_central_prison_link_left_behind_is_repaired(self):
        sub = self.fx.src_sub
        self.env.cr.execute('UPDATE prison_jail SET central_jail_id = NULL WHERE id = %s', (sub.id,))
        sub.invalidate_recordset()
        self.assertFalse(sub.central_jail_id)
        self.assertGreaterEqual(self.Jail._heal_central_links(), 1)
        self.assertEqual(sub.central_jail_id, self.fx.src_central)

    def test_match_name_updates_the_record_kept_under_another_spelling(self):
        sub = self.fx.src_sub
        row = {'name': 'R1Master Newspelling', 'jail_type': 'sub_jail', 'parent': self.parent,
               'match_name': sub.name, 'match_type': 'sub_jail'}
        result = self.Jail._reconcile_master([row], apply=True)
        self.assertNotIn('add', result['summary'])
        self.assertEqual((sub.name, sub.active), ('R1Master Newspelling', True))

    def test_running_a_list_with_old_spellings_twice_adds_nothing_the_second_time(self):
        sub = self.fx.src_sub
        row = {'name': 'R1Master Newspelling', 'jail_type': 'sub_jail', 'parent': self.parent,
               'match_name': sub.name, 'match_type': 'sub_jail', 'district': 'R1Master District'}
        self.Jail._reconcile_master([row], apply=True)
        again = self.Jail._reconcile_master([row], apply=True)
        self.assertNotIn('add', again['summary'])
        self.assertEqual(self.Jail.with_context(active_test=False).search_count(
            [('name', '=', 'R1Master Newspelling')]), 1)
        self.assertEqual((sub.name, sub.district), ('R1Master Newspelling', 'R1Master District'))

    def test_a_parent_name_shared_by_a_central_prison_and_a_women_prison_takes_the_row_hierarchy(self):
        self.Jail.create({'name': 'R1Master Twin', 'jail_type': 'central_jail'})
        spw = self.Jail.create({'name': 'R1Master Twin', 'jail_type': 'spw', 'hierarchy_type': 'women'})
        row = {'name': 'R1Master Womenpatti', 'jail_type': 'women_sub_jail', 'hierarchy_type': 'women',
               'parent': 'R1Master Twin'}
        self.Jail._reconcile_master([row], apply=True)
        self.assertEqual(self._find('R1Master Womenpatti').parent_id, spw)

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

    def test_the_dashboard_and_prisons_info_count_institutions_the_same_way(self):
        open_ones = self._get('/api/jails/hierarchy-with-vacancy').json()['stats']['total']
        summary = self._get('/api/dashboard/summary').json()
        self.assertGreaterEqual(summary['closed_institutions'], 1)  # the closed jail made for this test
        # every institution, closed ones included: Prisons Info's Total Institutions card
        self.assertEqual(summary['total_institutions'], open_ones + summary['closed_institutions'])

    def test_a_jail_under_a_switched_off_district_is_listed_not_hidden(self):
        off = self.env['prison.jail'].create({
            'name': 'R1Over Off District', 'jail_type': 'district_jail',
            'parent_id': self.fx.src_central.id, 'active': False})
        lost = self.env['prison.jail'].create({
            'name': 'R1Over Lost Sub', 'jail_type': 'sub_jail', 'parent_id': off.id})
        data = self._get('/api/jails/hierarchy-with-vacancy?include_closed=true').json()
        entry = next(u for u in data['unplaced'] if u['id'] == lost.id)
        self.assertTrue(entry['parent_inactive'])
        self.assertEqual(entry['parent_name'], 'R1Over Off District')
        # and nothing is both in the tree and in the list
        in_tree = {c['id'] for p in data['data'] for c in p['children']}
        self.assertNotIn(lost.id, in_tree)

    def test_a_placed_jail_is_not_in_the_unplaced_list(self):
        data = self._get('/api/jails/hierarchy-with-vacancy?include_closed=true').json()
        self.assertNotIn(self.closed.id, [u['id'] for u in data['unplaced']])

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
