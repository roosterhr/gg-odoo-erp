# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import html as html_lib
import io
import re
import shutil

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .common import approved_transfer, make_fixtures

REPORT = 'tnpd_hr_employee.report_transfer_offer_letter'


@tagged('post_install', '-at_install')
class TestTransferOfferLetter(TransactionCase):
    """The Transfer Offer Letter (proceedings) PDF of a transfer order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Letter')
        cls.Order = cls.env['transfer.order']
        cls.report_model = cls.env['report.tnpd_hr_employee.report_transfer_offer_letter']

    def _order(self, *officers, transfer_type='request'):
        transfers = self.env['transfer.approval.request']
        for officer in officers:
            transfers |= approved_transfer(self.env, self.fx, officer, transfer_type)
        return self.Order._draft_for_transfers(transfers)

    def _text(self, order):
        """The letter as a reader sees it: tags removed, spaces collapsed."""
        markup, _kind = self.env['ir.actions.report']._render_qweb_html(REPORT, order.ids)
        body = markup.decode().split('<body', 1)[-1]
        body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', body, flags=re.S)
        return re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', body))).replace(' )', ')').replace('( ', '(')

    # ── single officer ────────────────────────────────────────────────────────

    def test_letter_shows_the_officer_the_two_institutions_and_the_dates(self):
        order = self._order(self.fx.officers[0])
        order.action_issue()
        text = self._text(order)
        approved_on = fields.Datetime.context_timestamp(
            order.transfer_ids, order.transfer_ids.approved_date).strftime('%d.%m.%Y')
        for expected in (
            'Letter Officer 1', 'LETTER-0001', 'Grade I Warder (Men)',
            'Sub-Jail, Letter Source Sub', 'Sub-Jail, Letter Target Sub',
            'No. ' + order.name, 'Dated: ' + order.order_date.strftime('%d.%m.%Y'),
            'Transfer approved on ' + approved_on, 'Director General of Prisons',
        ):
            self.assertIn(expected, text)

    def test_from_is_the_posting_saved_on_the_request(self):
        order = self._order(self.fx.officers[0])
        # Approval moves the employee to the new posting; the letter must still
        # say where the officer came from.
        self.fx.officers[0].write({
            'x_central_jail_id': self.fx.dst_central.id, 'x_sub_jail_id': self.fx.dst_sub.id,
        })
        values = self.report_model._letter_values(order)
        self.assertEqual(values['source'], 'Sub-Jail, Letter Source Sub')
        self.assertEqual(values['target'], 'Sub-Jail, Letter Target Sub')
        self.assertEqual(values['addressed_to'], [
            'Central Prison, Letter Source Central', 'Central Prison, Letter Target Central',
        ])

    def test_own_request_mentions_ta_da_and_administrative_grounds_does_not(self):
        own = self.report_model._letter_values(self._order(self.fx.officers[0], transfer_type='request'))
        self.assertTrue(any('not eligible to claim any TA/DA' in c for c in own['clauses']))

        admin = self.report_model._letter_values(self._order(self.fx.officers[1], transfer_type='admin_grounds'))
        self.assertFalse(any('TA/DA' in c for c in admin['clauses']))
        self.assertTrue(any('administrative grounds' in c for c in admin['clauses']))
        self.assertTrue(admin['show_ref'])

    def test_ta_da_follows_the_flag_on_the_transfer(self):
        order = self._order(self.fx.officers[0], transfer_type='request')
        order.transfer_ids.ta_da_eligible = True
        values = self.report_model._letter_values(order)
        self.assertFalse(any('TA/DA' in c for c in values['clauses']))

    def test_clauses_are_numbered_in_order(self):
        text = self._text(self._order(self.fx.officers[0]))
        self.assertIn('1) Tr. Letter Officer 1', text)
        for number in ('2)', '3)', '4)', '5)'):
            self.assertIn(number, text)
        self.assertNotIn('6)', text)

    def test_the_blanks_filled_in_by_hand_are_printed(self):
        order = self._order(self.fx.officers[0])
        order.write({
            'present': 'Thiru. Test Present', 'dig_range': 'Vellore',
            'reference_letter': '123/2026', 'reference_date': '2026-03-05',
        })
        text = self._text(order)
        for expected in ('Present: Thiru. Test Present', 'Prisons, Vellore Range',
                         'Letter No. 123/2026', 'Dated 05.03.2026'):
            self.assertIn(expected, text)

    # ── draft and issued ──────────────────────────────────────────────────────

    def test_draft_is_marked_and_has_no_number_until_issued(self):
        order = self._order(self.fx.officers[0])
        self.assertIn('DRAFT', self._text(order))
        order.action_issue()
        text = self._text(order)
        self.assertNotIn('DRAFT', text)
        self.assertIn('No. ' + order.name, text)

    def test_cancelled_order_is_not_printed(self):
        order = self._order(self.fx.officers[0])
        order.action_cancel()
        with self.assertRaises(UserError):
            self._text(order)

    # ── bulk list ─────────────────────────────────────────────────────────────

    def test_bulk_letter_lists_every_officer_with_ta_da(self):
        order = self._order(*self.fx.officers)
        order.transfer_ids[0].ta_da_eligible = True
        text = self._text(order)
        self.assertEqual(order.layout, 'bulk')
        for officer in self.fx.officers:
            self.assertIn(officer.name, text)
            self.assertIn('Employee ID (%s)' % officer.x_employee_code, text)
        self.assertIn('Transfer and postings of Grade I Warders (Men)', text)
        self.assertIn('The following Grade I Warders (Men) are transferred', text)
        self.assertIn('The Superintendents are requested to relieve', text)
        self.assertIn('Central Prison, Letter Source Central and Central Prison, Letter Target Central', text)
        rows = self.report_model._letter_values(order)['rows']
        self.assertEqual([r['ta_da'] for r in rows], [True, False, False])

    def test_plural_of_a_designation(self):
        plural = self.report_model._plural
        self.assertEqual(plural('Grade I Warder (Men)'), 'Grade I Warders (Men)')
        self.assertEqual(plural('Secretary'), 'Secretaries')
        self.assertEqual(plural('Boss'), 'Bosses')
        self.assertEqual(plural('Day'), 'Days')
        self.assertEqual(plural(''), 'officers')

    # ── PDF ───────────────────────────────────────────────────────────────────

    def test_pdf_is_produced(self):
        if not shutil.which('wkhtmltopdf'):
            self.skipTest('wkhtmltopdf is not installed here')
        order = self._order(self.fx.officers[0])
        report = self.env['ir.actions.report'].with_context(force_report_rendering=True)
        pdf, kind = report._render_qweb_pdf(REPORT, order.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_pdf_text_is_not_garbled(self):
        """Dashes must stay dashes: a wrong text encoding shows up as 'a-circumflex, euro'."""
        try:
            from pdfminer.high_level import extract_text
        except ImportError:
            self.skipTest('pdfminer is not installed here')
        if not shutil.which('wkhtmltopdf'):
            self.skipTest('wkhtmltopdf is not installed here')
        order = self._order(self.fx.officers[0])
        report = self.env['ir.actions.report'].with_context(force_report_rendering=True)
        pdf, _kind = report._render_qweb_pdf(REPORT, order.ids)
        text = extract_text(io.BytesIO(pdf))
        self.assertIn('Services – Tamil Nadu', text)
        self.assertNotIn('â€', text)
        self.assertNotIn('Â', text)
