# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo.tests import TransactionCase, tagged

from .common import approved_transfer, make_fixtures


@tagged('post_install', '-at_install')
class TestOrderLetterRecipients(TransactionCase):
    """The people on the letter (Superintendent, DIG, IG, DSP) are told when it is issued."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Letter')
        Employee = cls.env['hr.employee']

        def post(code, designation, central=None, **extra):
            return Employee.create({
                'name': '%s %s' % (cls.fx.tag, code), 'x_employee_code': 'LTR-%s' % code,
                'x_designation': designation, 'x_central_jail_id': central.id if central else False,
                **extra,
            })

        cls.super_src = post('SUP1', 'Superintendent of Prisons (Men)', cls.fx.src_central)
        cls.super_dst = post('SUP2', 'Superintendent of Prisons (Men)', cls.fx.dst_central)
        cls.super_other = post('SUP3', 'Superintendent of Prisons (Men)',
                               cls.env['prison.jail'].create({'name': 'Letter Elsewhere', 'jail_type': 'central_jail'}))
        cls.deputy_super = post('DSUP', 'Deputy Superintendent of Prisons', cls.fx.src_central)
        cls.dig_here = post('DIG1', 'Deputy Inspector General of Prisons', cls.fx.src_central)
        cls.dig_far = post('DIG2', 'Deputy Inspector General of Prisons', cls.fx.dst_central)
        cls.ig = post('IG', 'Inspector General of Prisons (H.Q)')
        cls.dsp = post('DSP', 'Deputy Superintendent of Police (i/c)')
        cls.transfer = approved_transfer(cls.env, cls.fx, cls.fx.officers[0])
        cls.order = cls.env['transfer.order']._draft_for_transfers(cls.transfer)
        cls.order.dig_range = cls.fx.src_central.name

    def _notices(self, order=None):
        return self.env['tnpd.notification'].search([
            ('order_id', '=', (order or self.order).id), ('action_type', '=', 'transfer_order_copy'),
        ])

    def test_the_letter_goes_to_the_posts_on_it(self):
        recipients = self.order._letter_recipients()
        self.assertEqual(recipients[self.super_src], 'to')
        self.assertEqual(recipients[self.super_dst], 'to')
        self.assertEqual(recipients[self.dig_here], 'copy')
        self.assertEqual(recipients[self.ig], 'copy')
        self.assertEqual(recipients[self.dsp], 'copy')

    def test_nobody_else_is_told(self):
        recipients = self.order._letter_recipients()
        self.assertNotIn(self.super_other, recipients)   # another prison
        self.assertNotIn(self.deputy_super, recipients)  # a deputy is not the Superintendent
        self.assertNotIn(self.dig_far, recipients)       # another range
        self.assertNotIn(self.fx.officers[0], recipients)  # the officer has his own notice

    def test_no_range_means_no_dig(self):
        self.order.dig_range = False
        self.assertNotIn(self.dig_here, self.order._letter_recipients())

    def test_issuing_tells_them_and_lets_them_download(self):
        self.order.action_issue()
        told = self._notices().employee_id
        self.assertEqual(told, self.super_src | self.super_dst | self.dig_here | self.ig | self.dsp)
        self.assertEqual(self.order._letter_recipient_ids(), told)
        self.assertNotIn(self.super_other, self.order._letter_recipient_ids())

    def test_the_notice_says_whether_it_is_to_you_or_a_copy(self):
        self.order.action_issue()
        by_employee = {n.employee_id: n.message for n in self._notices()}
        self.assertIn('addressed to you', by_employee[self.super_src])
        self.assertIn('copied to you', by_employee[self.ig])
        self.assertIn(self.order.name, by_employee[self.ig])

    def test_cancelling_an_issued_order_tells_them_too(self):
        self.order.action_issue()
        before = len(self._notices())
        self.order.action_cancel()
        self.assertTrue(any('cancelled' in n.message for n in self._notices()))
        self.assertGreater(len(self._notices()), before)

    def test_a_missing_post_never_blocks_issuing(self):
        (self.ig | self.dsp | self.dig_here | self.super_src | self.super_dst).write({'active': False})
        self.order.action_issue()
        self.assertEqual(self.order.state, 'issued')
        self.assertFalse(self._notices())

    def test_a_draft_tells_nobody(self):
        self.assertFalse(self._notices())
