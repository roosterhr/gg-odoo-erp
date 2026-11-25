# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Linking officers to the institutions their typed posting names stand for."""

from odoo.tests import TransactionCase, tagged

from ..models.posting_link import posting_key

SCOPE = [('x_employee_code', '=like', 'R1LINK-%')]


@tagged('post_install', '-at_install')
class TestPostingKey(TransactionCase):

    def test_type_words_word_order_and_numbering_do_not_matter(self):
        same = {'Chennai - II', 'CENTRAL PRISON - II CHENNAI', 'Central Prison, Chennai-2',
                'Central Prison - 2 Chennai', 'central prison II, chennai'}
        self.assertEqual({posting_key(name) for name in same}, {posting_key('Chennai - II')})
        self.assertNotEqual(posting_key('Chennai - I'), posting_key('Chennai - II'))
        self.assertEqual(posting_key('Central Prison, Chennai-1'), posting_key('Chennai - I'))
        self.assertEqual(posting_key('Central Prison, Chennai - 01'), posting_key('Chennai - I'))

    def test_known_abbreviations_and_old_spellings(self):
        self.assertEqual(posting_key('SPW, Trichy'), posting_key('Special Prison for Women, Tiruchirappalli'))
        self.assertEqual(posting_key('PALAYAMCOTTAI'), posting_key('Palayamkottai'))
        self.assertEqual(posting_key('S.J. Ambur'), posting_key('Sub Jail, Ambur'))


@tagged('post_install', '-at_install')
class TestLinkPostings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Jail = cls.env['prison.jail']
        cls.two = Jail.create({'name': 'R1Link Place - II', 'jail_type': 'central_jail'})
        cls.one = Jail.create({'name': 'R1Link Place - I', 'jail_type': 'central_jail'})
        cls.other = Jail.create({'name': 'R1Link Otherplace', 'jail_type': 'central_jail'})
        cls.sub_a = Jail.create({'name': 'R1Link Subplace Tirupathur', 'jail_type': 'sub_jail', 'parent_id': cls.two.id})
        cls.sub_c = Jail.create({'name': 'R1Link Subplace Tiruppattur', 'jail_type': 'sub_jail', 'parent_id': cls.other.id})
        cls.spw = Jail.create({'name': 'R1Link Womens Home', 'jail_type': 'spw', 'hierarchy_type': 'women'})
        cls.wsj = Jail.create({'name': 'R1Link Wsubplace', 'jail_type': 'women_sub_jail',
                               'hierarchy_type': 'women', 'parent_id': cls.spw.id})
        cls.count = 0

    def _officer(self, central=None, sub=None, link_central=None, designation='Grade II Warder (Men)'):
        type(self).count += 1
        return self.env['hr.employee'].create({
            'name': 'R1Link Officer %s' % self.count, 'x_employee_code': 'R1LINK-%04d' % self.count,
            'x_designation': designation, 'x_central_prison': central or False, 'x_sub_jail': sub or False,
            'x_central_jail_id': link_central.id if link_central else False})

    def _run(self, **kw):
        return self.env['hr.employee']._link_postings_to_institutions(domain=SCOPE, **kw)

    def test_spellings_word_order_and_numbering_find_the_same_central_prison(self):
        officers = [self._officer(central=text) for text in (
            'Central Prison, R1Link Place-2', 'CENTRAL PRISON - II R1LINK PLACE',
            'Central Prison - 2 R1Link Place', 'central prison ii, r1link place')]
        result = self._run()
        self.assertEqual(result['links'], {'central': 4})
        self.assertEqual([o.x_central_jail_id for o in officers], [self.two] * 4)

    def test_a_name_that_fits_two_institutions_is_left_alone(self):
        officer = self._officer(central='Central Prison, R1Link Place')  # I or II?
        self._run()
        self.assertFalse(officer.x_central_jail_id)

    def test_an_existing_link_is_never_changed(self):
        officer = self._officer(central='Central Prison, R1Link Place-2', link_central=self.one)
        self._run()
        self.assertEqual(officer.x_central_jail_id, self.one)

    def test_a_similar_name_under_another_central_prison_is_not_chosen(self):
        in_a = self._officer(sub='S.J. R1Link Subplace Tirupattur', link_central=self.two)
        in_c = self._officer(sub='S.J. R1Link Subplace Tirupattur', link_central=self.other)
        self._run()
        self.assertEqual(in_a.x_sub_jail_id, self.sub_a)
        self.assertEqual(in_c.x_sub_jail_id, self.sub_c)

    def test_a_sub_jail_of_another_central_prison_is_skipped(self):
        officer = self._officer(sub='S.J. R1Link Subplace Tiruppattur', link_central=self.two)
        self._run()
        self.assertFalse(officer.x_sub_jail_id)

    def test_the_sub_jail_column_can_name_a_women_prison(self):
        officer = self._officer(sub='SPW, R1Link Womens Home', designation='Grade I Warder (Women)')
        self._run()
        self.assertEqual(officer.x_central_jail_id, self.spw)

    def test_a_womens_institution_is_not_given_to_a_men_officer(self):
        man = self._officer(sub='Sub Jail for Women, R1Link Wsubplace', designation='Grade II Warder (Men)')
        woman = self._officer(sub='Sub Jail for Women, R1Link Wsubplace', designation='Grade II Warder (Women)')
        self._run()
        self.assertFalse(man.x_sub_jail_id)
        self.assertEqual(woman.x_sub_jail_id, self.wsj)

    def test_a_dry_run_writes_nothing_and_a_second_run_changes_nothing(self):
        officer = self._officer(central='Central Prison, R1Link Place-2')
        dry = self._run(dry_run=True)
        self.assertEqual(dry['links'], {'central': 1})
        self.assertFalse(officer.x_central_jail_id)
        self.assertEqual(self._run()['links'], {'central': 1})
        self.assertEqual(officer.x_central_jail_id, self.two)
        self.assertEqual(self._run()['officers'], 0)

    def test_a_district_link_that_points_at_a_central_prison_moves_to_the_central_link(self):
        officer = self.env['hr.employee'].create({
            'name': 'R1Link Moved', 'x_employee_code': 'R1LINK-9001', 'x_district_jail_id': self.two.id})
        self.assertEqual(self.env['hr.employee']._heal_posting_levels() >= 1, True)
        self.assertEqual((officer.x_central_jail_id, officer.x_district_jail_id), (self.two, self.env['prison.jail']))

    def test_what_could_not_be_placed_is_reported(self):
        self._officer(central='Central Prison, Nowhere Atall')
        self.assertIn(('central: Central Prison, Nowhere Atall', 1), self._run()['unplaced'])
