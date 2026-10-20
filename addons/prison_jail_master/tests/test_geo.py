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


if __name__ == '__main__':
    unittest.main()
