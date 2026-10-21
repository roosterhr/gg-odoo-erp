# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOfficerDistance(TransactionCase):
    """Stored distance on hr.employee follows the native district and the posting."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Jail = cls.env['prison.jail']
        # Cuddalore centre, with a district jail and a sub-jail that borrow it.
        cls.central = Jail.create({
            'name': 'Dist Central', 'jail_type': 'central_jail',
            'latitude': 11.7480, 'longitude': 79.7714,
        })
        cls.district = Jail.create({
            'name': 'Dist District', 'jail_type': 'district_jail', 'parent_id': cls.central.id,
        })
        cls.sub = Jail.create({
            'name': 'Dist Sub', 'jail_type': 'sub_jail', 'parent_id': cls.district.id,
        })

    def _officer(self, native, **jails):
        return self.env['hr.employee'].create(dict({
            'name': 'Test Officer %s' % native,
            'x_employee_code': 'DIST-%s' % native,
            'x_native_district': native,
        }, **jails))

    def test_vellore_officer_at_cuddalore_is_yellow(self):
        emp = self._officer('Vellore', x_central_jail_id=self.central.id)
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.assertAlmostEqual(emp.x_distance_km, 147, delta=2)
        self.assertFalse(emp.x_distance_approx)

    def test_spelling_variants_and_aliases_match(self):
        a = self._officer('Thiruvannamalai', x_central_jail_id=self.central.id)
        b = self._officer('Tiruvannamalai', x_central_jail_id=self.central.id)
        self.assertEqual(a.x_distance_km, b.x_distance_km)
        self.assertNotEqual(a.x_distance_band, 'unknown')

    def test_unknown_when_district_missing_or_unrecognised(self):
        for native in ('', 'Nowhereville'):
            emp = self._officer(native or 'blank', x_central_jail_id=self.central.id)
            emp.x_native_district = native
            self.assertEqual(emp.x_distance_band, 'unknown')
            self.assertFalse(emp.x_distance_approx)

    def test_posting_at_a_sub_jail_borrows_up_to_the_central_and_is_approximate(self):
        emp = self._officer(
            'Vellore', x_central_jail_id=self.central.id, x_sub_jail_id=self.sub.id)
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.assertTrue(emp.x_distance_approx)

    def test_distance_refreshes_when_a_parent_two_levels_up_moves(self):
        emp = self._officer(
            'Vellore', x_central_jail_id=self.central.id, x_sub_jail_id=self.sub.id)
        self.assertEqual(emp.x_distance_band, 'yellow')
        # Move the central prison to Vellore itself: the sub-jail officer, two
        # levels down, must drop to green without being touched.
        self.central.write({'latitude': 12.9165, 'longitude': 79.1325})
        self.assertEqual(emp.x_distance_band, 'green')
        self.assertLessEqual(emp.x_distance_km, 5)

    def test_distance_refreshes_when_the_native_district_changes(self):
        emp = self._officer('Vellore', x_central_jail_id=self.central.id)
        emp.x_native_district = 'Cuddalore'
        self.assertEqual(emp.x_distance_band, 'green')
        self.assertEqual(emp.x_distance_km, 0)

    def test_editing_the_district_table_refreshes_officers(self):
        emp = self._officer('Vellore', x_central_jail_id=self.central.id)
        vellore = self.env['prison.district.geo'].search([('name', '=', 'Vellore')], limit=1)
        before = emp.x_distance_km
        vellore.write({'latitude': vellore.latitude + 1.0})
        self.assertNotEqual(emp.x_distance_km, before)

    def test_api_filter_domain(self):
        self._officer('Vellore', x_central_jail_id=self.central.id)
        Emp = self.env['hr.employee']
        yellow = Emp.search([('x_distance_band', '=', 'yellow'), ('x_employee_code', 'like', 'DIST-')])
        self.assertEqual(len(yellow), 1)
