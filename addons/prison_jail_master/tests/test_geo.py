# Part of TNPD Prison Management System.
# License: LGPL-3

"""
Unit tests for the distance rules in ``geo.py``.

They need no database, so they also run outside Odoo:

    python addons/prison_jail_master/tests/test_geo.py
"""

import importlib.util
import pathlib
import unittest

try:
    from odoo.addons.prison_jail_master import geo
except ImportError:  # running outside Odoo
    _path = pathlib.Path(__file__).resolve().parents[1] / 'geo.py'
    _spec = importlib.util.spec_from_file_location('geo', _path)
    geo = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(geo)


class TestDistanceBand(unittest.TestCase):

    def test_band_edges(self):
        self.assertEqual(geo.distance_band(0), 'green')
        self.assertEqual(geo.distance_band(70), 'green')
        self.assertEqual(geo.distance_band(71), 'yellow')
        self.assertEqual(geo.distance_band(150), 'yellow')
        self.assertEqual(geo.distance_band(151), 'red')
        self.assertEqual(geo.distance_band(538), 'red')

    def test_band_uses_whole_km(self):
        # 70.4 shows as "70 km" and must be green; 70.5 shows as "71 km".
        self.assertEqual(geo.distance_band(70.4), 'green')
        self.assertEqual(geo.distance_band(70.5), 'yellow')
        self.assertEqual(geo.distance_band(150.4), 'yellow')
        self.assertEqual(geo.distance_band(150.5), 'red')

    def test_unknown(self):
        self.assertEqual(geo.distance_band(None), 'unknown')

    def test_whole_km_rounds_half_up(self):
        self.assertEqual(geo.whole_km(70.5), 71)
        self.assertEqual(geo.whole_km(71.5), 72)
        self.assertEqual(geo.whole_km(0.2), 0)


class TestHaversine(unittest.TestCase):

    def test_same_point_is_zero(self):
        self.assertEqual(geo.haversine_km(11.75, 79.77, 11.75, 79.77), 0)

    def test_vellore_to_cuddalore(self):
        km = geo.haversine_km(12.9165, 79.1325, 11.7480, 79.7714)
        self.assertAlmostEqual(km, 147.5, delta=2)
        self.assertEqual(geo.distance_band(km), 'yellow')

    def test_chennai_to_kanniyakumari_is_red(self):
        km = geo.haversine_km(13.0827, 80.2707, 8.1833, 77.4119)
        self.assertGreater(km, 500)
        self.assertEqual(geo.distance_band(km), 'red')


class TestAgreedPoint(unittest.TestCase):

    def test_nothing_or_one(self):
        self.assertIsNone(geo.agreed_point([]))
        self.assertIsNone(geo.agreed_point(None))
        self.assertEqual(geo.agreed_point([(11.0, 79.0, False)]), (11.0, 79.0, False))

    def test_points_in_one_place_agree(self):
        near = [(8.5000, 78.1200, False), (8.5080, 78.1250, True)]
        self.assertEqual(geo.agreed_point(near), (8.5, 78.12, False))

    def test_points_in_different_places_do_not(self):
        self.assertIsNone(geo.agreed_point([(10.0, 78.0, False), (12.0, 80.0, False)]))


class TestPlaceKey(unittest.TestCase):

    def test_spelling_variants_match(self):
        pairs = [
            ('Thiruvannamalai', 'Tiruvannamalai'),
            ('Villupuram', 'Viluppuram'),
            ('Kancheepuram', 'Kanchipuram'),
            ('Thoothukudi', 'Thoothukkudi'),
            ('Pudukkottai', 'Pudukottai'),
            ('Tirupattur', 'Tirupathur'),
            ('Thiruvallur', 'Tiruvallur'),
        ]
        for a, b in pairs:
            self.assertEqual(geo.place_key(a), geo.place_key(b), (a, b))

    def test_noise_words_and_case(self):
        self.assertEqual(geo.place_key('VELLORE District'), geo.place_key('vellore'))
        self.assertEqual(geo.place_key('  Cuddalore  '), geo.place_key('Cuddalore'))

    def test_different_places_differ(self):
        self.assertNotEqual(geo.place_key('Salem'), geo.place_key('Namakkal'))
        self.assertNotEqual(geo.place_key('Tirunelveli'), geo.place_key('Tenkasi'))

    def test_blank(self):
        self.assertEqual(geo.place_key(''), '')
        self.assertEqual(geo.place_key(None), '')

    def test_find_point(self):
        table = {geo.place_key('Vellore'): (12.9165, 79.1325)}
        self.assertEqual(geo.find_point(table, 'VELLORE'), (12.9165, 79.1325))
        self.assertIsNone(geo.find_point(table, 'Madurai'))
        self.assertIsNone(geo.find_point(table, ''))
        self.assertIsNone(geo.find_point(table, None))


class TestFindDistrictPoint(unittest.TestCase):
    """Native places are typed in by hand: misspelt, with extra words, or a town."""

    VELLORE = (12.9165, 79.1325)
    DINDIGUL = (10.3673, 77.9803)
    TIRUNELVELI = (8.7139, 77.7567)
    TIRUPPUR = (11.1085, 77.3411)
    TIRUPATTUR = (12.4955, 78.5679)

    def setUp(self):
        self.table = {}
        for label, point in (
            ('Vellore', self.VELLORE), ('Dindigul', self.DINDIGUL),
            ('Tirunelveli', self.TIRUNELVELI), ('Palayamkottai', self.TIRUNELVELI),
            ('Tiruppur', self.TIRUPPUR), ('Tirupattur', self.TIRUPATTUR),
        ):
            self.table[geo.place_key(label)] = point

    def find(self, text):
        return geo.find_district_point(self.table, text)

    def test_exact_and_district_words(self):
        for text in ('Vellore', 'VELLORE DIST', 'Vellore District.', 'VellureDt', 'Vellore (Dt)'):
            self.assertEqual(self.find(text), self.VELLORE, text)

    def test_a_town_listed_as_a_spelling(self):
        self.assertEqual(self.find('Palayamkottai'), self.TIRUNELVELI)

    def test_the_district_is_found_inside_longer_text(self):
        self.assertEqual(self.find('Katpadi, Vellore'), self.VELLORE)
        self.assertEqual(self.find('Tirunelveli / Radhapuram'), self.TIRUNELVELI)
        self.assertEqual(self.find('VIRUTHAMPATTU vellore district'), self.VELLORE)

    def test_misspellings(self):
        self.assertEqual(self.find('Dindugul'), self.DINDIGUL)
        self.assertEqual(self.find('TIRUNELVILI'), self.TIRUNELVELI)
        self.assertEqual(self.find('Tirunalvelli'), self.TIRUNELVELI)

    def test_close_names_of_two_places_are_not_guessed(self):
        """A typo almost as close to Tiruppur as to Tirupattur is left unknown."""
        self.assertEqual(self.find('Tiruppur'), self.TIRUPPUR)
        self.assertEqual(self.find('Tirupattur'), self.TIRUPATTUR)
        self.assertEqual(self.find('Tirupatpur'), self.TIRUPATTUR, 'a clear winner is matched')
        self.assertIsNone(self.find('Tirupaur'))

    def test_single_words_are_not_matched_loosely(self):
        self.assertIsNone(self.find('Vellure Madhura'))

    def test_short_or_empty_names_are_not_matched_loosely(self):
        for text in ('', None, '--', 'NIL', '2017-18 / 56', 'Vel'):
            self.assertIsNone(self.find(text), text)


class TestPlaceCore(unittest.TestCase):
    """The place part of an institution name, without words that only name its kind."""

    def test_prefixes_and_suffixes_are_ignored(self):
        same = [
            'Coimbatore', 'Central Prison, Coimbatore', 'central prison  coimbatore',
            'S.J. Coimbatore', 'Coimbatore District', 'D.J. Coimbatore',
            'S.J. Coimbatore (Closed)', 'SPW, Coimbatore', 'Coimbatore (Women)',
        ]
        keys = {geo.place_core(n) for n in same}
        self.assertEqual(keys, {geo.place_key('Coimbatore')})

    def test_spelling_variants_still_match(self):
        self.assertEqual(geo.place_core('S.J. Thiruvannamalai'), geo.place_core('Tiruvannamalai District'))

    def test_roman_numbers_and_other_words_are_kept(self):
        self.assertEqual(geo.place_core('Chennai - I'), geo.place_core('Chennai - II'),
                         'collapsing repeated letters merges these; they share a town')
        self.assertNotEqual(geo.place_core('S.J. Salem'), geo.place_core('S.J. Namakkal'))
        self.assertNotEqual(geo.place_core('D.J. Thoothukudi @ Perurani'), geo.place_core('Thoothukudi District'))

    def test_a_name_made_only_of_kind_words_has_no_place(self):
        self.assertEqual(geo.place_core('Central Prison'), '')
        self.assertEqual(geo.place_core(''), '')
        self.assertEqual(geo.place_core(None), '')


if __name__ == '__main__':
    unittest.main()
