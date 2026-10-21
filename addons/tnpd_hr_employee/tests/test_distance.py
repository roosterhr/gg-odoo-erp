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
            'name': 'Gamma Central', 'jail_type': 'central_jail',
            'latitude': 11.7480, 'longitude': 79.7714,
        })
        cls.district = Jail.create({
            'name': 'Gamma District', 'jail_type': 'district_jail', 'parent_id': cls.central.id,
        })
        cls.sub = Jail.create({
            'name': 'Gamma Sub', 'jail_type': 'sub_jail', 'parent_id': cls.district.id,
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

    def test_native_place_typed_by_hand(self):
        """Misspellings, extra words and towns resolve to their district."""
        expected = {
            'Vellore': 'Vellore', 'Katpadi, Vellore': 'Vellore', 'VELLORE DIST.': 'Vellore',
            'Dindugal': 'Dindigul', 'Dindukkal': 'Dindigul', 'Viruthunagar': 'Virudhunagar',
            'Sivakasi': 'Virudhunagar', 'Tuticurin District': 'Thoothukudi',
        }
        for native, district in expected.items():
            emp = self._officer(native, x_central_jail_id=self.central.id)
            reference = self._officer(district, x_central_jail_id=self.central.id)
            self.assertNotEqual(emp.x_distance_band, 'unknown', native)
            self.assertEqual(emp.x_distance_km, reference.x_distance_km, native)

    def test_native_place_that_names_nothing_is_unknown(self):
        for native in ('NIL', '--', 'Madhura koothiramedu'):
            emp = self._officer(native, x_central_jail_id=self.central.id)
            self.assertEqual(emp.x_distance_band, 'unknown', native)

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

    # ── Posting recorded only as a name in the old text fields ───────────────

    def test_posting_named_in_text_gets_a_distance(self):
        emp = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.assertFalse(emp.x_distance_approx)

    def test_text_sub_jail_borrows_its_parent_and_is_approximate(self):
        emp = self._officer('Vellore', x_sub_jail='S.J. Gamma')
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.assertTrue(emp.x_distance_approx)

    def test_unknown_text_name_falls_back_to_the_next_one_up_as_approximate(self):
        emp = self._officer(
            'Vellore', x_sub_jail='S.J. Nowhere', x_central_jail_id=self.central.id)
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.assertTrue(emp.x_distance_approx)

    def test_text_name_that_matches_nothing_is_unknown(self):
        emp = self._officer('Vellore', x_central_prison='Central Prison, Nowhere')
        self.assertEqual(emp.x_distance_band, 'unknown')

    def test_text_name_that_matches_two_far_apart_places_is_not_guessed(self):
        Jail = self.env['prison.jail']
        Jail.create({'name': 'Central Prison, Twin', 'jail_type': 'central_jail',
                     'latitude': 9.0, 'longitude': 78.0})
        Jail.create({'name': 'Twin', 'jail_type': 'central_jail',
                     'latitude': 13.0, 'longitude': 80.0})
        emp = self._officer('Vellore', x_central_prison='Twin')
        self.assertEqual(emp.x_distance_band, 'unknown')

    def test_text_posted_officer_follows_the_institution(self):
        emp = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.central.write({'latitude': 12.9165, 'longitude': 79.1325})
        self.assertEqual(emp.x_distance_band, 'green')

    def test_text_posted_officer_follows_a_new_institution(self):
        emp = self._officer('Vellore', x_central_prison='Central Prison, Latecomer')
        self.assertEqual(emp.x_distance_band, 'unknown')
        self.env['prison.jail'].create({
            'name': 'Latecomer', 'jail_type': 'central_jail',
            'latitude': 12.9165, 'longitude': 79.1325,
        })
        self.assertEqual(emp.x_distance_band, 'green')

    def _women_prison(self, name='Gamma'):
        # A women's prison and a central prison often carry the same town name.
        return self.env['prison.jail'].create({
            'name': name, 'jail_type': 'spw', 'hierarchy_type': 'women',
            'latitude': 8.1833, 'longitude': 77.4119,
        })

    def test_women_prison_with_the_same_town_name_is_not_ambiguous(self):
        self._women_prison()
        emp = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        self.assertEqual(emp.x_distance_band, 'yellow', 'the central prison, not the women\'s one')

    def test_a_name_that_says_women_looks_among_the_women_prisons(self):
        self._women_prison()
        emp = self._officer('Vellore', x_central_prison='Special Prison for Women, Gamma')
        self.assertEqual(emp.x_distance_band, 'red', 'Kanniyakumari is far from Vellore')

    def test_a_women_prison_is_used_when_no_central_prison_has_that_name(self):
        self._women_prison('Delta')
        emp = self._officer('Vellore', x_central_prison='Delta')
        self.assertEqual(emp.x_distance_band, 'red')

    # ── Only the officers who are affected are refreshed ──────────────────────

    def test_a_change_refreshes_only_officers_posted_by_that_name(self):
        affected = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        other = self._officer('Madurai', x_central_prison='Central Prison, Elsewhere')
        unrelated = self._officer('Salem', x_central_jail_id=self.central.id)
        for emp in (other, unrelated):
            emp.x_distance_km = 999  # a marker: a refresh would overwrite it
        self.central.write({'latitude': 12.9165, 'longitude': 79.1325})
        self.assertEqual(affected.x_distance_band, 'green')
        self.assertEqual(other.x_distance_km, 999, 'posted elsewhere: not recomputed')
        # the linked officer is refreshed by the field dependency, not by name
        self.assertNotEqual(unrelated.x_distance_km, 999)

    def test_a_renamed_institution_refreshes_both_its_old_and_new_names(self):
        old = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        new = self._officer('Vellore', x_central_prison='Central Prison, Renamed')
        self.assertEqual(old.x_distance_band, 'yellow')
        self.assertEqual(new.x_distance_band, 'unknown')
        self.central.write({'name': 'Renamed'})
        self.assertEqual(old.x_distance_band, 'unknown')
        self.assertEqual(new.x_distance_band, 'yellow')

    def test_a_sub_jail_name_follows_a_parent_that_moves(self):
        emp = self._officer('Vellore', x_sub_jail='S.J. Gamma')
        self.assertEqual(emp.x_distance_band, 'yellow')
        self.central.write({'latitude': 12.9165, 'longitude': 79.1325})
        self.assertEqual(emp.x_distance_band, 'green', 'named after a sub-jail two levels down')

    def test_institutions_with_an_empty_parent_path_can_still_be_edited(self):
        """Some databases have a few institutions without a parent_path."""
        emp = self._officer('Vellore', x_sub_jail='S.J. Gamma')
        self.env.cr.execute(
            "UPDATE prison_jail SET parent_path = NULL WHERE id IN %s",
            [tuple((self.central | self.district | self.sub).ids)],
        )
        self.env['prison.jail'].invalidate_model(['parent_path'])
        self.central.write({'latitude': 12.9165, 'longitude': 79.1325})
        self.central.write({'name': 'Gamma Central Renamed'})
        self.assertEqual(emp.x_distance_band, 'green', 'the sub-jail below still follows')
        self.sub.unlink()
        self.assertEqual(emp.x_distance_band, 'unknown', 'the typed name no longer matches anything')

    def test_bulk_loading_can_skip_the_refresh_and_do_it_once_at_the_end(self):
        emp = self._officer('Vellore', x_central_prison='Central Prison, Gamma')
        Jail = self.env['prison.jail'].with_context(skip_officer_distance=True)
        Jail.browse(self.central.id).write({'latitude': 12.9165, 'longitude': 79.1325})
        self.assertEqual(emp.x_distance_band, 'yellow', 'skipped')
        Jail._refresh_officer_distances()
        self.assertEqual(emp.x_distance_band, 'green')

    def test_api_filter_domain(self):
        self._officer('Vellore', x_central_jail_id=self.central.id)
        Emp = self.env['hr.employee']
        yellow = Emp.search([('x_distance_band', '=', 'yellow'), ('x_employee_code', 'like', 'DIST-')])
        self.assertEqual(len(yellow), 1)
