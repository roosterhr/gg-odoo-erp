# Part of TNPD Prison Management System.
# License: LGPL-3

from unittest import mock

from odoo.tests import TransactionCase, tagged

from odoo.addons.prison_jail_master import geo_seed
from odoo.addons.prison_jail_master.geo_seed import seed_from_rows


def _row(name, jail_type, lat='11.0', lng='79.0', hierarchy='general', xml_id='nowhere.none'):
    """A seed-file row. Tests build their own, so they do not depend on the database."""
    return {
        'xml_id': xml_id, 'name': name, 'jail_type': jail_type,
        'hierarchy_type': hierarchy, 'latitude': lat, 'longitude': lng, 'method': 'test',
    }


@tagged('post_install', '-at_install')
class TestPrisonGeoPosition(TransactionCase):
    """Effective position of an institution."""

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
        _central, district, sub = self._hierarchy()
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


@tagged('post_install', '-at_install')
class TestGeoSeeding(TransactionCase):
    """Starting coordinates are found whatever a database calls its institutions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Jail = cls.env['prison.jail']
        cls.parent = cls.Jail.create({'name': 'Zed Parent', 'jail_type': 'central_jail'})

    def _jail(self, name, jail_type='sub_jail', **vals):
        if jail_type != 'central_jail':
            vals.setdefault('parent_id', self.parent.id)
        return self.Jail.create(dict(vals, name=name, jail_type=jail_type))

    def _placed(self, jail):
        return bool(jail.latitude and jail.longitude)

    def test_exact_name_when_the_xml_id_is_gone(self):
        jail = self._jail('Zedville Annex Town')
        seed_from_rows(self.env, [_row('Zedville Annex Town', 'sub_jail')])
        self.assertEqual((jail.latitude, jail.longitude), (11.0, 79.0))
        self.assertEqual(jail.geo_source, 'approximate')

    def test_central_prison_named_without_the_prefix(self):
        jail = self._jail('Zedville', 'central_jail')
        seed_from_rows(self.env, [_row('Central Prison, Zedville', 'central_jail')])
        self.assertTrue(self._placed(jail))

    def test_sub_and_district_prefixes(self):
        sub = self._jail('Zedton')
        district = self._jail('Zedburg Jail', 'district_jail')
        seed_from_rows(self.env, [
            _row('S.J. Zedton', 'sub_jail'),
            _row('Zedburg District', 'district_jail', lat='12.0', lng='80.0'),
        ])
        self.assertTrue(self._placed(sub))
        self.assertEqual((district.latitude, district.longitude), (12.0, 80.0))

    def test_spelling_variant_of_the_place(self):
        jail = self._jail('Thiruzedpuram')
        seed_from_rows(self.env, [_row('S.J. Tiruzedpuram', 'sub_jail')])
        self.assertTrue(self._placed(jail))

    def test_institution_type_is_respected(self):
        """A district jail must not take the position meant for a sub-jail of the same place."""
        district = self._jail('Zedwick District', 'district_jail')
        seed_from_rows(self.env, [_row('S.J. Zedwick', 'sub_jail')])
        self.assertFalse(self._placed(district))

    def test_open_and_closed_jail_in_one_town_share_a_position(self):
        open_jail = self._jail('Zedford')
        old_jail = self._jail('S.J. Zedford', active=False)
        seed_from_rows(self.env, [
            _row('S.J. Zedford', 'sub_jail'),
            _row('S.J. Zedford (Closed)', 'sub_jail'),
        ])
        self.assertTrue(self._placed(open_jail))
        self.assertTrue(self._placed(old_jail), 'archived institutions are seeded too')

    def test_rows_a_kilometre_apart_are_one_place(self):
        """The same town looked up twice can differ by a km; that is not ambiguity."""
        jail = self._jail('Zedshore')
        seed_from_rows(self.env, [
            _row('S.J. Zedshore', 'sub_jail', lat='8.5000', lng='78.1200'),
            _row('S.J. Zedshore (Closed)', 'sub_jail', lat='8.5080', lng='78.1250'),
        ])
        self.assertEqual((jail.latitude, jail.longitude), (8.5, 78.12))

    def test_ambiguous_place_is_not_guessed(self):
        """Two seed rows that spell alike but sit in different towns: place nothing."""
        jail = self._jail('Zedpurr')
        seed_from_rows(self.env, [
            _row('S.J. Zedpur', 'sub_jail', lat='10.0', lng='78.0'),
            _row('S.J. Zedpoor', 'sub_jail', lat='12.0', lng='80.0'),
        ])
        self.assertFalse(self._placed(jail))

    def test_only_one_of_its_kind(self):
        farm = self._jail('Zed Home Farm', 'farm_jail')
        seed_from_rows(self.env, [_row('Quite Another Name', 'farm_jail')])
        self.assertTrue(self._placed(farm))

    def test_never_overwrites_a_position_that_is_already_set(self):
        jail = self._jail('Zedset', latitude=9.5, longitude=78.0)
        seed_from_rows(self.env, [_row('S.J. Zedset', 'sub_jail')])
        self.assertEqual((jail.latitude, jail.longitude), (9.5, 78.0))
        self.assertFalse(jail.geo_source)

    def test_a_warning_is_logged_when_institutions_cannot_be_placed(self):
        jail = self._jail('Zedlost')
        with mock.patch.object(geo_seed, 'WARN_MISSING_SHARE', 0.0), \
                self.assertLogs('odoo.addons.prison_jail_master.geo_seed', level='WARNING') as logs:
            # two rows of the kind, so "the only one of its kind" cannot apply
            seed_from_rows(self.env, [
                _row('S.J. Somewhere Else', 'sub_jail'),
                _row('S.J. Another Place', 'sub_jail', lat='12.0', lng='80.0'),
            ])
        self.assertFalse(self._placed(jail))
        self.assertTrue(any('Distance unknown' in line for line in logs.output))
