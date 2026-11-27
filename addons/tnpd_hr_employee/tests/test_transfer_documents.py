# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import json

from odoo.tests import HttpCase, TransactionCase, tagged

from .. import transfer_documents as td
from .common import make_fixtures

PDF = b'%PDF-1.4\n% a supporting document\n'
JPG = b'\xff\xd8\xff\xe0' + b'0' * 40
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 40


def _pdf(name='medical.pdf'):
    return ('files', (name, PDF, 'application/pdf'))


@tagged('post_install', '-at_install')
class TestDocumentRules(TransactionCase):
    """Which reasons need documents, and what a file must look like."""

    def test_a_request_needs_documents_unless_every_reason_is_single_parent(self):
        codes = self.env['tnpd.transfer.reason'].search([('selectable', '=', True)]).mapped('code')
        self.assertIn('single_parent', codes)
        for code in codes:
            self.assertEqual(td.reasons_need_documents([code]), code != 'single_parent', code)
        self.assertFalse(td.reasons_need_documents(['single_parent']))
        self.assertTrue(td.reasons_need_documents(['single_parent', 'illness_self']))
        self.assertTrue(td.reasons_need_documents(['illness_self', 'ex_serviceman']))
        self.assertFalse(td.reasons_need_documents([]), 'no reason: the reason check answers first')

    def test_pdf_jpg_and_png_are_accepted(self):
        for name, data, mime in (('a.pdf', PDF, 'application/pdf'), ('a.JPG', JPG, 'image/jpeg'),
                                 ('a.jpeg', JPG, 'image/jpeg'), ('a.png', PNG, 'image/png')):
            self.assertEqual(td.check_file(name, data), (name, mime), name)

    def test_other_types_are_refused(self):
        for name, data in (('run.exe', b'MZ\x90\x00'), ('page.html', b'<html>'), ('note.txt', b'hello'),
                           ('noext', PDF)):
            with self.assertRaises(td.UploadError, msg=name):
                td.check_file(name, data)

    def test_the_content_must_match_the_extension(self):
        for name, data in (('fake.pdf', b'<html><script>x</script>'), ('fake.png', PDF), ('fake.jpg', PNG)):
            with self.assertRaises(td.UploadError, msg=name):
                td.check_file(name, data)

    def test_empty_and_oversize_files_are_refused(self):
        with self.assertRaises(td.UploadError):
            td.check_file('empty.pdf', b'')
        with self.assertRaises(td.UploadError):
            td.check_file('big.pdf', PDF + b'0' * td.MAX_BYTES)
        self.assertEqual(td.check_file('ok.pdf', (PDF + b'0' * td.MAX_BYTES)[:td.MAX_BYTES])[0], 'ok.pdf')

    def test_file_names_lose_folders_and_odd_characters(self):
        self.assertEqual(td.check_file('../../etc/passwd/medical.pdf', PDF)[0], 'medical.pdf')
        self.assertEqual(td.check_file('C:\\Users\\x\\scan 1.pdf', PDF)[0], 'scan 1.pdf')
        self.assertEqual(td.check_file('a<b>"c".pdf', PDF)[0], 'a_b_c_.pdf')
        long_name = td.check_file('x' * 300 + '.pdf', PDF)[0]
        self.assertLessEqual(len(long_name), 100)
        self.assertTrue(long_name.endswith('.pdf'))

    def test_a_request_holds_at_most_three_files(self):
        self.assertEqual(td.MAX_FILES, 3)
        full = [('%d.pdf' % i, PDF) for i in range(td.MAX_FILES)]
        self.assertEqual(len(td.validate_files(full)), 3)
        with self.assertRaises(td.UploadError):
            td.validate_files(full + [('extra.pdf', PDF)])
        with self.assertRaises(td.UploadError):
            td.validate_files([('extra.pdf', PDF)], already=td.MAX_FILES)


@tagged('post_install', '-at_install')
class TestTransferDocumentsModel(TransactionCase):
    """Documents are plain attachments on the transfer request."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'Doc')

    def _request(self, officer, reason='illness_self'):
        """A request with one reason (or a list of reason codes, as the officer can now choose several)."""
        codes = [reason] if isinstance(reason, str) else list(reason)
        reasons = self.env['tnpd.transfer.reason'].search([('code', 'in', codes)])
        self.assertEqual(len(reasons), len(codes), 'unknown reason in the test')
        return self.env['transfer.approval.request'].create({
            'employee_id': officer.id, 'transfer_type': 'request', 'reason_ids': [(6, 0, reasons.ids)],
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'state': 'pending',
        })

    def test_files_are_stored_on_the_request_in_order(self):
        req = self._request(self.fx.officers[0])
        req._add_documents([('one.pdf', PDF), ('two.png', PNG)])
        docs = req._documents()
        self.assertEqual(docs.mapped('name'), ['one.pdf', 'two.png'])
        self.assertEqual(docs.mapped('mimetype'), ['application/pdf', 'image/png'])
        self.assertFalse(any(docs.mapped('public')))
        self.assertEqual(docs[0].raw, PDF)

    def test_documents_by_request_covers_every_request(self):
        a = self._request(self.fx.officers[0])
        b = self._request(self.fx.officers[1])
        a._add_documents([('a.pdf', PDF)])
        found = (a | b)._documents_by_request()
        self.assertEqual(found[a.id].mapped('name'), ['a.pdf'])
        self.assertFalse(found[b.id])

    def test_a_bad_file_saves_nothing(self):
        req = self._request(self.fx.officers[0])
        with self.assertRaises(td.UploadError):
            req._add_documents([('good.pdf', PDF), ('bad.exe', b'MZ\x90')])
        self.assertFalse(req._documents())

    def test_the_total_cannot_pass_three_across_calls(self):
        req = self._request(self.fx.officers[0])
        req._add_documents([('%d.pdf' % i, PDF) for i in range(2)])
        with self.assertRaises(td.UploadError):
            req._add_documents([('x.pdf', PDF), ('y.pdf', PDF)])
        self.assertEqual(len(req._documents()), 2)
        req._add_documents([('z.pdf', PDF)])
        self.assertEqual(len(req._documents()), 3)

    def test_the_request_knows_if_it_needs_documents(self):
        self.assertTrue(self._request(self.fx.officers[0], 'illness_self')._needs_documents())
        self.assertFalse(self._request(self.fx.officers[1], 'single_parent')._needs_documents())

    def test_several_reasons_need_documents_unless_all_are_single_parent(self):
        self.assertTrue(self._request(self.fx.officers[0], ['single_parent', 'illness_self'])._needs_documents())
        self.assertTrue(self._request(self.fx.officers[1], ['illness_self', 'ex_serviceman'])._needs_documents())

    def test_an_older_request_with_only_the_single_reason_is_judged_by_it(self):
        old = self.env['transfer.approval.request'].create({
            'employee_id': self.fx.officers[2].id, 'transfer_type': 'request', 'reason_category': 'single_parent',
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
            'approval_user_id': self.env.user.id, 'state': 'pending',
        })
        self.assertFalse(old.reason_ids)
        self.assertFalse(old._needs_documents())


@tagged('post_install', '-at_install')
class TestTransferDocumentsAPI(HttpCase):
    """The officer's routes and the admin's file route."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'DocApi')
        # Three destinations the officer can choose from.
        cls.subs = [cls.env['prison.jail'].create({
            'name': 'DocApi Dest %d' % i, 'jail_type': 'sub_jail', 'parent_id': cls.fx.dst_central.id,
        }) for i in range(3)]
        Users = cls.env['res.users']
        group_user = cls.env.ref('base.group_user')
        cls.admin = Users.create({
            'name': 'Doc Admin', 'login': 'doc_admin', 'password': 'Doc-Admin-1',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('base.group_erp_manager').id])],
        })
        cls.officer_user = Users.create({
            'name': 'Doc Officer', 'login': 'doc_officer', 'password': 'Doc-Officer-1',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        cls.other_user = Users.create({
            'name': 'Doc Other', 'login': 'doc_other', 'password': 'Doc-Other-1',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        cls.fx.officers[0].user_id = cls.officer_user
        cls.fx.officers[1].user_id = cls.other_user

    # ── helpers ───────────────────────────────────────────────────────────────

    def _payload(self, reason):
        """``reason`` is one code, or a list of codes (an officer can choose several)."""
        s = self.subs
        chosen = {'reason_categories': reason} if isinstance(reason, list) else {'reason_category': reason}
        return {
            **chosen,
            'to_central_jail_id': self.fx.dst_central.id, 'to_sub_jail_id': s[0].id,
            'p2_central_jail_id': self.fx.dst_central.id, 'p2_sub_jail_id': s[1].id,
            'p3_central_jail_id': self.fx.dst_central.id, 'p3_sub_jail_id': s[2].id,
        }

    def _submit(self, reason, files=()):
        """Submit through the officer's API the way the browser does: multipart."""
        return self.url_open(
            '/api/employee-portal/transfers',
            data={'payload': json.dumps(self._payload(reason))},
            files=list(files) or None,
        )

    def _delete(self, path):
        return self.opener.delete(self.base_url() + path, timeout=12)

    def _requests_of(self, officer):
        return self.env['transfer.approval.request'].search([('employee_id', '=', officer.id)])

    def _login_officer(self):
        self.authenticate('doc_officer', 'Doc-Officer-1')

    # ── submitting ────────────────────────────────────────────────────────────

    def test_a_required_reason_cannot_be_submitted_without_a_document(self):
        self._login_officer()
        reply = self._submit('illness_self').json()
        self.assertFalse(reply['success'], reply)
        self.assertIn('supporting document', reply['message'])
        self.assertFalse(self._requests_of(self.fx.officers[0]))

    def test_every_reason_but_single_parent_is_refused_without_a_document(self):
        self._login_officer()
        reasons = set(self.env['tnpd.transfer.reason'].search([('selectable', '=', True)]).mapped('code')) - {'single_parent'}
        self.assertTrue(reasons)
        for reason in reasons:
            payload = dict(self._payload(reason), transfer_reason='because')
            reply = self.url_open('/api/employee-portal/transfers', data={'payload': json.dumps(payload)}).json()
            self.assertFalse(reply['success'], reason)
            self.assertIn('supporting document', reply['message'], reason)
        self.assertFalse(self._requests_of(self.fx.officers[0]))

    def test_single_parent_needs_no_document(self):
        self._login_officer()
        reply = self._submit('single_parent').json()
        self.assertTrue(reply['success'], reply)
        self.assertEqual(reply['documents_count'], 0)
        self.assertFalse(self.env['transfer.approval.request'].browse(reply['request_id'])._documents())

    def test_a_request_with_documents_is_saved_with_them(self):
        self._login_officer()
        reply = self._submit('illness_self', [_pdf('medical.pdf'), ('files', ('proof.png', PNG, 'image/png'))]).json()
        self.assertTrue(reply['success'], reply)
        self.assertEqual(reply['documents_count'], 2)
        req = self.env['transfer.approval.request'].browse(reply['request_id'])
        self.assertEqual(req._documents().mapped('name'), ['medical.pdf', 'proof.png'])
        note = self.env['tnpd.notification'].search([('transfer_request_id', '=', req.id)], limit=1)
        self.assertIn('2 supporting documents attached', note.message)

    def test_a_bad_file_refuses_the_whole_request(self):
        self._login_officer()
        reply = self._submit('illness_self', [_pdf(), ('files', ('virus.pdf', b'MZ\x90\x00', 'application/pdf'))]).json()
        self.assertFalse(reply['success'], reply)
        self.assertFalse(self._requests_of(self.fx.officers[0]))

    def test_plain_json_still_works_for_single_parent(self):
        self._login_officer()
        reply = self.url_open(
            '/api/employee-portal/transfers', data=json.dumps(self._payload('single_parent')),
            headers={'Content-Type': 'application/json'}).json()
        self.assertTrue(reply['success'], reply)

    def test_officer_lists_names_and_sizes_but_has_no_way_to_open_a_file(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf()]).json()['request_id']
        mine = self.url_open('/api/employee-portal/transfers').json()['transfers'][0]
        self.assertEqual((mine['documents_count'], mine['documents_required'], mine['documents_editable']), (1, True, True))
        self.assertEqual(mine['documents'][0]['name'], 'medical.pdf')
        # The officer's login is refused on the admin file route.
        doc_id = mine['documents'][0]['id']
        self.assertEqual(self.url_open('/api/transfer/%s/documents/%s' % (rid, doc_id)).status_code, 403)

    def test_single_parent_together_with_another_reason_needs_a_document(self):
        self._login_officer()
        reply = self._submit(['single_parent', 'illness_self']).json()
        self.assertFalse(reply['success'], reply)
        self.assertIn('supporting document', reply['message'])
        self.assertFalse(self._requests_of(self.fx.officers[0]))
        sent = self._submit(['single_parent', 'illness_self'], [_pdf()]).json()
        self.assertTrue(sent['success'], sent)
        mine = self.url_open('/api/employee-portal/transfers').json()['transfers'][0]
        self.assertTrue(mine['documents_required'])

    def test_a_list_holding_only_single_parent_needs_no_document(self):
        self._login_officer()
        reply = self._submit(['single_parent']).json()
        self.assertTrue(reply['success'], reply)
        mine = self.url_open('/api/employee-portal/transfers').json()['transfers'][0]
        self.assertFalse(mine['documents_required'])

    # ── adding and removing while pending ─────────────────────────────────────

    def test_add_and_remove_while_pending_and_keep_the_last_one(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf('one.pdf')]).json()['request_id']
        base = '/api/employee-portal/transfers/%s/documents' % rid

        added = self.url_open(base, files=[_pdf('two.pdf')]).json()
        self.assertTrue(added['success'], added)
        self.assertEqual([d['name'] for d in added['documents']], ['one.pdf', 'two.pdf'])

        first, second = (d['id'] for d in added['documents'])
        removed = self._delete('%s/%s' % (base, first)).json()
        self.assertTrue(removed['success'], removed)
        last = self._delete('%s/%s' % (base, second)).json()
        self.assertFalse(last['success'])
        self.assertIn('required', last['message'])
        self.assertEqual(len(self.env['transfer.approval.request'].browse(rid)._documents()), 1)

    def test_the_last_file_can_go_when_the_reason_needs_none(self):
        self._login_officer()
        rid = self._submit('single_parent', [_pdf()]).json()['request_id']
        doc = self.env['transfer.approval.request'].browse(rid)._documents()
        reply = self._delete('/api/employee-portal/transfers/%s/documents/%s' % (rid, doc.id)).json()
        self.assertTrue(reply['success'], reply)

    def test_nothing_changes_after_a_decision(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf()]).json()['request_id']
        req = self.env['transfer.approval.request'].browse(rid)
        req.write({'state': 'rejected'})
        base = '/api/employee-portal/transfers/%s/documents' % rid
        self.assertFalse(self.url_open(base, files=[_pdf('late.pdf')]).json()['success'])
        self.assertFalse(self._delete('%s/%s' % (base, req._documents().id)).json()['success'])
        listing = self.url_open(base).json()
        self.assertFalse(listing['documents_editable'])
        self.assertEqual(len(listing['documents']), 1)

    def test_an_officer_cannot_touch_someone_elses_request(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf()]).json()['request_id']
        doc = self.env['transfer.approval.request'].browse(rid)._documents()
        self.authenticate('doc_other', 'Doc-Other-1')
        base = '/api/employee-portal/transfers/%s/documents' % rid
        self.assertEqual(self.url_open(base).status_code, 404)
        self.assertEqual(self.url_open(base, files=[_pdf('x.pdf')]).status_code, 404)
        self.assertEqual(self._delete('%s/%s' % (base, doc.id)).status_code, 404)
        self.assertEqual(len(doc.exists()), 1)

    def test_signed_out_callers_are_refused(self):
        self.assertEqual(self.url_open('/api/employee-portal/transfers/1/documents').status_code, 401)
        self.assertEqual(self.url_open('/api/transfer/1/documents/1').status_code, 401)

    # ── the admin side ────────────────────────────────────────────────────────

    def test_admin_sees_the_files_in_the_list_and_can_open_them(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf('medical.pdf')]).json()['request_id']
        doc = self.env['transfer.approval.request'].browse(rid)._documents()

        self.authenticate('doc_admin', 'Doc-Admin-1')
        listing = self.url_open('/api/transfer/list-all?employee_id=%s' % self.fx.officers[0].id).json()
        row = next(r for r in listing['records'] if r['request_id'] == rid)
        self.assertEqual((row['documents_count'], row['documents_required']), (1, True))
        self.assertEqual(row['documents'][0]['name'], 'medical.pdf')

        shown = self.url_open('/api/transfer/%s/documents/%s' % (rid, doc.id))
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.headers['Content-Type'], 'application/pdf')
        self.assertTrue(shown.headers['Content-Disposition'].startswith('inline'))
        self.assertEqual(shown.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(shown.content, PDF)

        saved = self.url_open('/api/transfer/%s/documents/%s?download=1' % (rid, doc.id))
        self.assertTrue(saved.headers['Content-Disposition'].startswith('attachment'))

    def test_the_admin_route_answers_404_for_a_wrong_request_or_file(self):
        self._login_officer()
        rid = self._submit('illness_self', [_pdf()]).json()['request_id']
        doc = self.env['transfer.approval.request'].browse(rid)._documents()
        self.authenticate('doc_admin', 'Doc-Admin-1')
        self.assertEqual(self.url_open('/api/transfer/%s/documents/%s' % (rid, doc.id + 99999)).status_code, 404)
        self.assertEqual(self.url_open('/api/transfer/%s/documents/%s' % (rid + 99999, doc.id)).status_code, 404)
