# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo.tests import HttpCase, TransactionCase, tagged

from .common import approved_transfer, http_login, make_fixtures


def _posts(cls, fx):
    """The people on the letter: employee records of those posts, each with an admin login."""
    Employee = cls.env['hr.employee']
    erp = cls.env.ref('base.group_erp_manager')

    def post(code, designation, central=None, login=True, scope='hq', jails=()):
        emp = Employee.create({
            'name': '%s %s' % (fx.tag, code), 'x_employee_code': 'LTR-%s-%s' % (fx.tag, code),
            'x_designation': designation, 'x_central_jail_id': central.id if central else False,
        })
        if login:
            emp.user_id = cls.env['res.users'].create({
                'name': emp.name, 'login': 'ltr_%s_%s@test.local' % (fx.tag.lower(), code.lower()),
                'password': 'ltr_%s_%s@test.local' % (fx.tag.lower(), code.lower()),
                'group_ids': [(4, erp.id)], 'x_access_scope': scope, 'x_invited_by_admin': True,
                'x_jurisdiction_jail_ids': [(6, 0, [j.id for j in jails])],
            })
        return emp

    cls.post = staticmethod(post)


@tagged('post_install', '-at_install')
class TestOrderLetterRecipients(TransactionCase):
    """The people on the letter (Superintendent, DIG, IG, DSP) are told in the Admin login."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Letter')
        _posts(cls, cls.fx)
        cls.super_src = cls.post('SUP1', 'Superintendent of Prisons (Men)', cls.fx.src_central)
        cls.super_dst = cls.post('SUP2', 'Superintendent of Prisons (Men)', cls.fx.dst_central)
        cls.super_other = cls.post('SUP3', 'Superintendent of Prisons (Men)',
                                   cls.env['prison.jail'].create({'name': 'Letter Elsewhere', 'jail_type': 'central_jail'}))
        cls.deputy_super = cls.post('DSUP', 'Deputy Superintendent of Prisons', cls.fx.src_central)
        cls.dig_here = cls.post('DIG1', 'Deputy Inspector General of Prisons', cls.fx.src_central)
        cls.dig_far = cls.post('DIG2', 'Deputy Inspector General of Prisons', cls.fx.dst_central)
        cls.ig = cls.post('IG', 'Inspector General of Prisons (H.Q)')
        cls.dsp = cls.post('DSP', 'Deputy Superintendent of Police (i/c), Intelligence-cum-Vigilance Wing')
        cls.transfer = approved_transfer(cls.env, cls.fx, cls.fx.officers[0])
        cls.order = cls.env['transfer.order']._draft_for_transfers(cls.transfer)
        cls.order.dig_range = cls.fx.src_central.name

    def _notices(self, order=None):
        return self.env['tnpd.notification'].search([
            ('order_id', '=', (order or self.order).id), ('action_type', '=', 'transfer_order_copy'),
        ])

    def test_the_letter_goes_to_the_posts_on_it(self):
        recipients = self.order._letter_recipients()
        self.assertEqual(recipients[self.super_src.user_id], 'to')
        self.assertEqual(recipients[self.super_dst.user_id], 'to')
        self.assertEqual(recipients[self.dig_here.user_id], 'copy')
        self.assertEqual(recipients[self.ig.user_id], 'copy')
        self.assertEqual(recipients[self.dsp.user_id], 'copy')

    def test_nobody_else_is_told(self):
        users = self.order._letter_recipients()
        self.assertNotIn(self.super_other.user_id, users)   # another prison
        self.assertNotIn(self.deputy_super.user_id, users)  # a deputy is not the Superintendent
        self.assertNotIn(self.dig_far.user_id, users)       # another range

    def test_no_range_means_no_dig(self):
        self.order.dig_range = False
        self.assertNotIn(self.dig_here.user_id, self.order._letter_recipients())

    def test_a_post_without_an_admin_login_is_left_out(self):
        no_login = self.post('NOLOGIN', 'Inspector General of Prisons', login=False)
        self.assertFalse(no_login.user_id)
        self.assertTrue(self.order._letter_recipients())  # the others are still found
        self.assertNotIn(False, [u.id for u in self.order._letter_recipients()])

    def test_the_notice_is_for_the_admin_user_not_the_employee(self):
        self.order.action_issue()
        for notice in self._notices():
            self.assertTrue(notice.user_id)
            self.assertFalse(notice.employee_id)

    def test_issuing_tells_them(self):
        self.order.action_issue()
        told = self._notices().user_id
        here = (self.super_src.user_id | self.super_dst.user_id | self.dig_here.user_id
                | self.ig.user_id | self.dsp.user_id)
        self.assertEqual(told & here, here)
        self.assertNotIn(self.super_other.user_id, told)
        self.assertEqual(self.order._letter_recipient_users(), told)

    def test_the_notice_says_whether_it_is_to_you_or_a_copy(self):
        self.order.action_issue()
        by_user = {n.user_id: n.message for n in self._notices()}
        self.assertIn('addressed to you', by_user[self.super_src.user_id])
        self.assertIn('copied to you', by_user[self.ig.user_id])
        self.assertIn(self.order.name, by_user[self.ig.user_id])

    def test_the_officers_are_still_told_in_the_employee_login(self):
        self.order.action_issue()
        officer_notice = self.env['tnpd.notification'].search([
            ('order_id', '=', self.order.id), ('employee_id', '=', self.fx.officers[0].id)])
        self.assertTrue(officer_notice)

    def test_cancelling_an_issued_order_tells_them_too(self):
        self.order.action_issue()
        before = len(self._notices())
        self.order.action_cancel()
        self.assertGreater(len(self._notices()), before)
        self.assertTrue(any('cancelled' in n.message for n in self._notices()))

    def test_a_missing_post_never_blocks_issuing(self):
        mine = self.ig | self.dsp | self.dig_here | self.super_src | self.super_dst
        mine.write({'active': False})
        self.order.action_issue()
        self.assertEqual(self.order.state, 'issued')
        self.assertFalse(self._notices().user_id & mine.user_id)

    def test_a_dsp_of_another_unit_is_not_told(self):
        other = self.post('DSP2', 'Deputy Superintendent of Police (Armed Reserve)')
        self.assertNotIn(other.user_id, self.order._letter_recipients())
        self.assertIn(self.dsp.user_id, self.order._letter_recipients())

    def test_the_dig_is_matched_by_the_range_not_by_part_of_a_name(self):
        north = self.env['prison.jail'].create({
            'name': '%s North Central Prison' % self.fx.src_central.name, 'jail_type': 'central_jail'})
        dig_north = self.post('DIG3', 'Deputy Inspector General of Prisons', north)
        self.order.dig_range = '%s Range' % self.fx.src_central.name
        recipients = self.order._letter_recipients()
        self.assertIn(self.dig_here.user_id, recipients)        # 'Letter Source Range' is 'Letter Source Central Prison'
        self.assertNotIn(dig_north.user_id, recipients)         # 'Letter Source North ...' is another place

    def test_an_officer_on_the_order_who_is_also_an_admin_is_not_told_twice(self):
        officer = self.fx.officers[0]
        officer.user_id = self.ig.user_id
        self.assertNotIn(self.ig.user_id, self.order._letter_recipients())

    def test_a_draft_tells_nobody(self):
        self.assertFalse(self._notices())


@tagged('post_install', '-at_install')
class TestAdminNotificationsHttp(HttpCase):
    """The bell of the Admin login: list, mark read, and open the PDF of the letter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'LetterHttp')
        _posts(cls, cls.fx)
        # a Superintendent of the NEW prison only: the transfer itself is outside his area
        cls.super_dst = cls.post('SUP', 'Superintendent of Prisons (Men)', cls.fx.dst_central,
                                 scope='regional', jails=cls.fx.dst_central)
        cls.ig = cls.post('IG', 'Inspector General of Prisons (H.Q)')
        cls.stranger = cls.post('NOBODY', 'Jailer (Men)', scope='regional', jails=cls.fx.dst_central)
        transfer = approved_transfer(cls.env, cls.fx, cls.fx.officers[0])
        cls.order = cls.env['transfer.order']._draft_for_transfers(transfer)

    def _sign_in(self, emp):
        http_login(self, emp.user_id.login)

    def test_the_bell_lists_my_notices_and_counts_the_unread(self):
        self.order.action_issue()
        self._sign_in(self.ig)
        data = self.url_open('/api/admin/notifications').json()
        self.assertEqual(data['unread_count'], 1)
        self.assertEqual(data['notifications'][0]['order_id'], self.order.id)
        self.assertIn('copied to you', data['notifications'][0]['message'])

    def test_somebody_else_sees_nothing(self):
        self.order.action_issue()
        self._sign_in(self.stranger)
        self.assertEqual(self.url_open('/api/admin/notifications').json()['notifications'], [])

    def test_a_regional_user_can_mark_a_notice_read(self):
        self.order.action_issue()
        self._sign_in(self.super_dst)
        note = self.url_open('/api/admin/notifications').json()['notifications'][0]
        self.url_open('/api/admin/notifications/%s/read' % note['id'], data='{}',
                      headers={'Content-Type': 'application/json'})
        self.assertEqual(self.url_open('/api/admin/notifications').json()['unread_count'], 0)

    def test_the_superintendent_can_open_the_pdf_even_outside_his_area(self):
        self.order.action_issue()
        self._sign_in(self.super_dst)
        self.assertEqual(self.url_open('/api/transfer/orders/%s/pdf' % self.order.id).status_code, 200)

    def test_somebody_not_on_the_letter_cannot_open_the_pdf(self):
        self.order.action_issue()
        self._sign_in(self.stranger)
        self.assertEqual(self.url_open('/api/transfer/orders/%s/pdf' % self.order.id).status_code, 404)

    def test_marking_somebody_elses_notice_read_is_a_404(self):
        self.order.action_issue()
        self._sign_in(self.ig)
        mine = self.url_open('/api/admin/notifications').json()['notifications'][0]
        self._sign_in(self.super_dst)
        reply = self.url_open('/api/admin/notifications/%s/read' % mine['id'], data='{}',
                              headers={'Content-Type': 'application/json'})
        self.assertEqual(reply.status_code, 404)

    def test_mark_all_read_clears_the_count(self):
        self.order.action_issue()
        self._sign_in(self.ig)
        self.url_open('/api/admin/notifications/read-all', data='{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(self.url_open('/api/admin/notifications').json()['unread_count'], 0)

    def test_a_regional_login_may_not_write_anywhere_else_under_the_notifications_path(self):
        self._sign_in(self.super_dst)
        for path in ('/api/admin/notifications/abc', '/api/admin/notifications/1/delete', '/api/admin/notifications'):
            status = self.url_open(path, data='{}', headers={'Content-Type': 'application/json'}).status_code
            self.assertIn(status, (403, 404, 405), path)  # refused, whether the route exists or not

    def test_a_cancelled_order_pdf_is_a_404_for_the_recipient(self):
        self.order.action_issue()
        self.order.action_cancel()
        self._sign_in(self.super_dst)
        self.assertEqual(self.url_open('/api/transfer/orders/%s/pdf' % self.order.id).status_code, 404)

    def test_the_recipient_exception_is_for_the_pdf_only(self):
        self.order.action_issue()
        self._sign_in(self.super_dst)
        self.assertEqual(self.url_open('/api/transfer/orders/%s' % self.order.id).status_code, 404)

    def test_an_employee_login_cannot_use_the_bell(self):
        portal = self.env['res.users'].create({
            'name': 'Portal', 'login': 'ltr_portal@test.local', 'password': 'ltr_portal@test.local',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        http_login(self, portal.login)
        self.assertEqual(self.url_open('/api/admin/notifications').status_code, 403)
