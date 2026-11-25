# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""The approved institution list (141) and the 3.0.0 migration that brings a database to it."""

import importlib.util
import json
import os

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

LIST_PATH = 'tnpd_hr_employee/data/institution_master_rows.json'
EXPECTED_BY_TYPE = {
    'central_jail': 10, 'spw': 5, 'district_jail': 13, 'sub_jail': 96, 'women_sub_jail': 9,
    'special_sub_jail': 4, 'open_air_jail': 2, 'farm_jail': 1, 'transit_yard': 1,
}


def _rows():
    with file_open(LIST_PATH) as fh:
        return json.load(fh)


def _migrate_function():
    with file_open(LIST_PATH) as fh:
        data_dir = os.path.dirname(fh.name)
    path = os.path.abspath(os.path.join(data_dir, '..', 'migrations', '19.0.3.0.0', 'post-migrate.py'))
    spec = importlib.util.spec_from_file_location('post_migrate_3_0_0', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged('post_install', '-at_install')
class TestApprovedList(TransactionCase):

    def test_the_list_has_the_approved_totals(self):
        rows = _rows()
        self.assertEqual(len(rows), 141)
        by_type = {}
        for row in rows:
            by_type[row['jail_type']] = by_type.get(row['jail_type'], 0) + 1
        self.assertEqual(by_type, EXPECTED_BY_TYPE)
        self.assertEqual(sum(1 for r in rows if r.get('status') == 'closed'), 17)

    def test_every_row_is_valid_and_every_parent_is_in_the_list(self):
        rows = _rows()
        types = {key for key, _label in self.env['prison.jail']._fields['jail_type'].selection}
        seen = set()
        parents = {r['name'] for r in rows if r['jail_type'] in ('central_jail', 'spw', 'district_jail')}
        for row in rows:
            self.assertIn(row['jail_type'], types, row['name'])
            self.assertIn(row.get('status', 'active'), ('active', 'closed'), row['name'])
            self.assertIn(row.get('hierarchy_type', 'general'), ('general', 'women'), row['name'])
            self.assertNotIn((row['jail_type'], row['name']), seen, 'listed twice: %s' % row['name'])
            seen.add((row['jail_type'], row['name']))
            if row['jail_type'] in ('central_jail', 'spw'):
                self.assertFalse(row.get('parent'), '%s heads a hierarchy and has no parent' % row['name'])
            else:
                self.assertIn(row.get('parent'), parents, '%s: parent not in the list' % row['name'])

    def test_pudukkottai_heads_the_delta_region_as_a_central_prison(self):
        rows = _rows()
        head = [r for r in rows if r['name'] == 'Pudukkottai' and r['jail_type'] == 'central_jail']
        self.assertEqual(len(head), 1)
        delta = [r for r in rows if r.get('parent') == 'Pudukkottai']
        self.assertGreaterEqual(len(delta), 14)  # the Delta sub jails and the Nagapattinam district jail

    def test_there_is_no_hill_prison_row_or_flag(self):
        self.assertNotIn('is_hill_station', self.env['prison.jail']._fields)
        self.assertFalse(any('hill' in json.dumps(r).lower() for r in _rows()))


@tagged('post_install', '-at_install')
class TestMigration(TransactionCase):

    def _totals(self):
        return self.env['prison.jail']._master_totals()

    def test_the_migration_brings_a_database_to_the_approved_totals(self):
        _migrate_function()(self.env.cr, '19.0.2.9.0')
        self.env.invalidate_all()
        totals = self._totals()
        self.assertEqual(totals['active_total'], 141)
        self.assertEqual(totals['closed'], 17)
        self.assertEqual(totals['open'], 124)
        self.assertEqual(totals['by_type'], EXPECTED_BY_TYPE)

    def test_running_it_again_changes_nothing_and_leaves_the_tree_sound(self):
        _migrate_function()(self.env.cr, '19.0.2.9.0')
        self.env.invalidate_all()
        again = self.env['prison.jail']._reconcile_master(_rows(), apply=False, deactivate_missing=True)
        self.assertEqual(set(again['summary']) - {'rows', 'errors', 'unchanged'}, set(), again['summary'])
        self.env.cr.execute("""SELECT COUNT(*) FROM prison_jail WHERE active AND (parent_path IS NULL
                               OR (parent_path NOT LIKE '%%/' || id || '/' AND parent_path <> id || '/'))""")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.env.cr.execute("""SELECT COUNT(*) FROM prison_jail c JOIN prison_jail p ON p.id = c.parent_id
                               WHERE c.active AND c.jail_type NOT IN ('central_jail', 'spw')
                                 AND c.central_jail_id IS DISTINCT FROM p.id""")
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_a_fresh_install_is_left_alone(self):
        before = self._totals()
        _migrate_function()(self.env.cr, None)
        self.env.invalidate_all()
        self.assertEqual(self._totals(), before)
