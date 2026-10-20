# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo.tests import TransactionCase, tagged

from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates


@tagged('post_install', '-at_install')
class TestPrisonGeoPosition(TransactionCase):
    """Effective position of an institution, and seeding of starting coordinates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Jail = cls.env['prison.jail']

    def _hierarchy(self):
        central = self.Jail.create({
            'name': 'Test Central', 'jail_type': 'central_jail',
            'latitude': 11.0, 'longitude': 79.0,
        })
        district = self.Jail.create({
            'name': 'Test District', 'jail_type': 'district_jail', 'parent_id': central.id,
        })
        sub = self.Jail.create({
            'name': 'Test Sub', 'jail_type': 'sub_jail', 'parent_id': district.id,
        })
        return central, district, sub

    def test_own_coordinates_are_used(self):
        central, _district, _sub = self._hierarchy()
        self.assertEqual((central.geo_latitude, central.geo_longitude), (11.0, 79.0))
        self.assertFalse(central.geo_is_approx)
        central.geo_source = 'approximate'
        self.assertTrue(central.geo_is_approx)

    def test_position_is_borrowed_through_three_levels(self):
        central, district, sub = self._hierarchy()
        for jail in (district, sub):
            self.assertEqual((jail.geo_latitude, jail.geo_longitude), (11.0, 79.0))
            self.assertTrue(jail.geo_is_approx, 'a borrowed position must be flagged')
        self.assertEqual(sub._geo_point(), (11.0, 79.0, True))

    def test_change_at_the_top_refreshes_every_level_below(self):
        central, district, sub = self._hierarchy()
        central.write({'latitude': 12.0, 'longitude': 80.0})
        self.assertEqual((district.geo_latitude, district.geo_longitude), (12.0, 80.0))
        self.assertEqual((sub.geo_latitude, sub.geo_longitude), (12.0, 80.0))
        # a child that gets its own position stops borrowing
        sub.write({'latitude': 10.5, 'longitude': 78.5})
        self.assertEqual((sub.geo_latitude, sub.geo_longitude), (10.5, 78.5))
        self.assertFalse(sub.geo_is_approx)

    def test_no_position_anywhere(self):
        central = self.Jail.create({'name': 'Test Empty', 'jail_type': 'central_jail'})
        self.assertIsNone(central._geo_point())

    def test_seed_matches_by_name_when_the_xml_id_is_gone(self):
        jail = self.Jail.search([('name', '=', 'Central Prison, Cuddalore')], limit=1)
        self.assertTrue(jail, 'seed data should provide this prison')
        # Simulate a rebuilt database: the XML ID no longer points at the record
        # and the position was never set.
        self.env['ir.model.data'].search(
            [('model', '=', 'prison.jail'), ('res_id', '=', jail.id)]).unlink()
        jail.write({'latitude': 0.0, 'longitude': 0.0, 'geo_source': False})
        placed = seed_prison_coordinates(self.env)
        self.assertGreaterEqual(placed, 1)
        self.assertTrue(jail.latitude and jail.longitude)
        self.assertEqual(jail.geo_source, 'approximate')

    def test_seed_never_overwrites_a_position_that_is_already_set(self):
        jail = self.Jail.search([('name', '=', 'Central Prison, Madurai')], limit=1)
        jail.write({'latitude': 9.5, 'longitude': 78.0, 'geo_source': False})
        seed_prison_coordinates(self.env)
        self.assertEqual((jail.latitude, jail.longitude), (9.5, 78.0))
        self.assertFalse(jail.geo_source)

    def test_seed_reaches_archived_institutions(self):
        closed = self.Jail.with_context(active_test=False).search(
            [('name', 'like', '(Closed)'), ('active', '=', False)], limit=1)
        if not closed:
            self.skipTest('no archived institution in this database')
        closed.write({'latitude': 0.0, 'longitude': 0.0, 'geo_source': False})
        seed_prison_coordinates(self.env)
        self.assertTrue(closed.latitude and closed.longitude)
