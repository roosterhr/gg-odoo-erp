# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""Client round 1: several reasons, the weighted score, service years, suggestions and documents."""

from datetime import date, timedelta

from odoo.tests import HttpCase, TransactionCase, tagged

from .common import http_login, make_fixtures

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


def _request(env, fx, officer, codes=(), state='pending', score_codes=None):
    reasons = env['tnpd.transfer.reason'].search([('code', 'in', list(codes))])
    return env['transfer.approval.request'].create({
        'employee_id': officer.id, 'transfer_type': 'request', 'state': state,
        'current_central_prison': fx.src_central.id, 'current_sub_jail': fx.src_sub.id,
        'requested_central_prison': fx.dst_central.id, 'requested_sub_jail': fx.dst_sub.id,
        'approval_user_id': env.user.id, 'requested_by': env.user.id,
        'reason_ids': [(6, 0, reasons.ids)],
    })


@tagged('post_install', '-at_install')
class TestSeveralReasons(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Reasons')
        cls.Request = cls.env['transfer.approval.request']

    def test_the_reasons_come_with_their_weights(self):
        weights = {r.code: r.score for r in self.env['tnpd.transfer.reason'].search([])}
        self.assertEqual(weights['illness_self'], 8)
        self.assertEqual(weights['mutual_request'], 1)
        self.assertEqual(weights['others'], 0)

    def test_one_reason_scores_exactly_its_weight(self):
        self.assertEqual(_request(self.env, self.fx, self.fx.officers[0], ['single_parent']).reason_score, 5)

    def test_several_reasons_combine_into_a_weighted_score(self):
        # heaviest in full (8) + half of the rest ((6 + 5) / 2 = 5.5) = 13.5, rounded up
        req = _request(self.env, self.fx, self.fx.officers[0], ['illness_self', 'spouse_working', 'single_parent'])
        self.assertEqual(req.reason_score, 14)

    def test_the_share_of_the_other_reasons_is_a_setting(self):
        self.env['ir.config_parameter'].sudo().set_param('tnpd.transfer.score_secondary_weight', '0')
        req = _request(self.env, self.fx, self.fx.officers[0], ['illness_self', 'spouse_working'])
        self.assertEqual(req.reason_score, 8)

    def test_a_weight_can_be_changed_without_code(self):
        self.env['tnpd.transfer.reason'].search([('code', '=', 'mutual_request')]).score = 4
        self.assertEqual(_request(self.env, self.fx, self.fx.officers[0], ['mutual_request']).reason_score, 4)

    def test_the_main_reason_is_the_heaviest(self):
        req = _request(self.env, self.fx, self.fx.officers[0], ['mutual_request', 'retirement_one_year'])
        self.assertEqual(req.reason_category, 'retirement_one_year')

    def test_an_old_single_reason_request_keeps_its_score(self):
        old = self.Request.create({
            'employee_id': self.fx.officers[1].id, 'transfer_type': 'request', 'state': 'pending',
            'reason_category': 'ex_serviceman', 'approval_user_id': self.env.user.id,
            'requested_central_prison': self.fx.dst_central.id, 'requested_sub_jail': self.fx.dst_sub.id,
        })
        self.assertFalse(old.reason_ids)
        self.assertEqual(old.reason_score, 4)
        self.assertEqual([r['code'] for r in old._reasons_payload()], ['ex_serviceman'])

    def test_the_payload_takes_a_list_a_comma_string_or_the_old_single_value(self):
        for data in ({'reason_categories': ['illness_self', 'others']},
                     {'reason_categories': 'illness_self,others'},
                     {'reason_category': 'illness_self'}):
            reasons, err = self.Request._reasons_from_payload(data, required=True)
            self.assertIsNone(err, data)
            self.assertIn('illness_self', reasons.mapped('code'))

    def test_a_retired_or_unknown_reason_is_refused(self):
        for bad in ('medical_reasons', 'nonsense'):
            _reasons, err = self.Request._reasons_from_payload({'reason_categories': [bad]})
            self.assertTrue(err, bad)

    def test_at_least_one_reason_is_asked_for_when_required(self):
        _reasons, err = self.Request._reasons_from_payload({}, required=True)
        self.assertEqual(err, 'Please select at least one reason')
        self.assertIsNone(self.Request._reasons_from_payload({})[1])


@tagged('post_install', '-at_install')
class TestServiceYearsAndEligibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Service')
        officer = cls.fx.officers[0]
        officer.write({'x_date_of_appointment': date.today() - timedelta(days=int(12.2 * 365.25)),
                       'x_date_present_station': date.today() - timedelta(days=int(4.1 * 365.25))})
        cls.req = _request(cls.env, cls.fx, officer, ['single_parent'])

    def test_total_and_current_jail_years(self):
        self.assertEqual(self.req.service_years_total, 12.2)
        self.assertEqual(self.req.service_years_current, 4.1)

    def test_nothing_is_checked_until_the_client_sets_the_thresholds(self):
        self.assertEqual(self.req._eligibility(), {'eligible': True, 'checks': []})

    def test_the_thresholds_decide_eligibility(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('tnpd.transfer.min_current_jail_years', '3')
        self.assertTrue(self.req._eligibility()['eligible'])
        params.set_param('tnpd.transfer.min_current_jail_years', '5')
        result = self.req._eligibility()
        self.assertFalse(result['eligible'])
        self.assertEqual(result['checks'][0]['actual'], 4.1)
        params.set_param('tnpd.transfer.min_current_jail_years', '0')
        params.set_param('tnpd.transfer.min_service_years', '15')
        self.assertFalse(self.req._eligibility()['eligible'])

    def test_an_officer_with_no_dates_has_zero_years(self):
        bare = _request(self.env, self.fx, self.fx.officers[2], ['others'])
        self.assertEqual((bare.service_years_total, bare.service_years_current), (0.0, 0.0))


@tagged('post_install', '-at_install')
class TestSuggestionsAndDocuments(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = make_fixtures(cls.env, 'R1Http')
        erp = cls.env.ref('base.group_erp_manager')
        cls.admin = cls.env['res.users'].create({
            'name': 'R1 admin', 'login': 'r1_admin@test.local', 'password': 'r1_admin@test.local',
            'group_ids': [(4, erp.id)], 'x_invited_by_admin': True})
        portal = cls.env.ref('base.group_portal')
        for officer in cls.fx.officers:
            officer.user_id = cls.env['res.users'].create({
                'name': officer.name, 'login': officer.x_employee_code, 'password': officer.x_employee_code,
                'group_ids': [(6, 0, [portal.id])]})
        cls.env['prison.vacancy'].create({
            'prison_id': cls.fx.dst_sub.id, 'prison_name': cls.fx.dst_sub.name, 'prison_type': 'sub_jail',
            'sanctioned_strength': 3, 'occupied_count': 2, 'vacancy_count': 1})
        o1, o2, o3 = cls.fx.officers
        for officer, years in ((o1, 2), (o2, 6), (o3, 6)):
            officer.x_date_present_station = date.today() - timedelta(days=int(years * 365.25))
        cls.low = _request(cls.env, cls.fx, o1, ['mutual_request'])
        cls.high = _request(cls.env, cls.fx, o2, ['illness_self'])
        cls.mid = _request(cls.env, cls.fx, o3, ['single_parent'])
        cls.fx.other_designation.name = cls.fx.other_designation.name  # a different designation, ignored below
        cls.other = _request(cls.env, cls.fx, cls.fx.other_designation, ['illness_self'])

    # ── suggestions ───────────────────────────────────────────────────────────

    def _suggestions(self, request):
        http_login(self, 'r1_admin@test.local')
        return self.url_open('/api/transfer/suggestions?request_id=%s' % request.id).json()

    def test_applicants_for_the_same_posting_are_ranked_by_score(self):
        data = self._suggestions(self.low)
        self.assertEqual([a['request_id'] for a in data['applicants']], [self.high.id, self.mid.id, self.low.id])
        self.assertEqual([a['rank'] for a in data['applicants']], [1, 2, 3])
        self.assertEqual(data['posting']['name'], self.fx.dst_sub.name)

    def test_another_designation_is_not_compared(self):
        ids = [a['request_id'] for a in self._suggestions(self.low)['applicants']]
        self.assertNotIn(self.other.id, ids)

    def test_as_many_are_suggested_as_there_are_vacancies(self):
        data = self._suggestions(self.low)
        self.assertEqual(data['vacancies'], 1)
        self.assertEqual([a['suggested'] for a in data['applicants']], [True, False, False])

    def test_the_suggestion_skips_applicants_who_are_not_eligible(self):
        self.env['ir.config_parameter'].sudo().set_param('tnpd.transfer.min_current_jail_years', '10')
        data = self._suggestions(self.low)
        self.assertFalse(any(a['suggested'] for a in data['applicants']))
        self.assertFalse(any(a['eligible'] for a in data['applicants']))

    def test_a_posting_with_no_vacancy_suggests_nobody(self):
        self.env['prison.vacancy'].search([('prison_id', '=', self.fx.dst_sub.id)]).write({'occupied_count': 3, 'vacancy_count': 0})
        self.assertFalse(any(a['suggested'] for a in self._suggestions(self.low)['applicants']))

    # ── documents ─────────────────────────────────────────────────────────────

    def _upload(self, request, name='cert.pdf', content=PDF):
        http_login(self, request.employee_id.x_employee_code)
        return self.url_open('/api/employee-portal/transfer/%s/documents' % request.id,
                             files={'file': (name, content)})

    def test_an_officer_can_attach_a_pdf_and_a_picture(self):
        self.assertTrue(self._upload(self.low).json()['success'])
        self.assertTrue(self._upload(self.low, 'proof.png', PNG).json()['success'])
        listing = self.url_open('/api/employee-portal/transfer/%s/documents' % self.low.id).json()
        self.assertEqual([d['name'] for d in listing['documents']], ['cert.pdf', 'proof.png'])

    def test_other_types_are_refused(self):
        reply = self._upload(self.low, 'run.exe', b'MZ\x90\x00').json()
        self.assertFalse(reply['success'])
        self.assertIn('PDF', reply['message'])

    def test_a_file_that_is_not_what_it_says_is_refused(self):
        self.assertFalse(self._upload(self.low, 'fake.pdf', b'<html>not a pdf</html>').json()['success'])

    def test_an_empty_file_is_refused(self):
        self.assertFalse(self._upload(self.low, 'empty.pdf', b'').json()['success'])

    def test_a_file_over_the_size_limit_is_refused(self):
        self.env['ir.config_parameter'].sudo().set_param('tnpd.transfer.doc_max_mb', '0.00001')
        self.assertIn('too large', self._upload(self.low).json()['message'])

    def test_at_most_the_limit_of_documents_per_request(self):
        self.env['ir.config_parameter'].sudo().set_param('tnpd.transfer.doc_max_files', '2')
        self._upload(self.low)
        self._upload(self.low)
        self.assertIn('At most 2', self._upload(self.low).json()['message'])

    def test_nobody_can_attach_to_somebody_elses_request(self):
        http_login(self, self.fx.officers[1].x_employee_code)
        reply = self.url_open('/api/employee-portal/transfer/%s/documents' % self.low.id,
                              files={'file': ('cert.pdf', PDF)})
        self.assertEqual(reply.status_code, 404)

    def test_documents_cannot_be_added_once_the_request_is_decided(self):
        self.low.state = 'approved'
        self.assertFalse(self._upload(self.low).json()['success'])

    def test_the_officer_can_remove_a_document(self):
        doc = self._upload(self.low).json()['document']
        reply = self.url_open('/api/employee-portal/transfer/%s/documents/%s' % (self.low.id, doc['id']), method='DELETE')
        self.assertTrue(reply.json()['success'])
        self.assertEqual(self.url_open('/api/employee-portal/transfer/%s/documents' % self.low.id).json()['documents'], [])

    def test_the_admin_sees_and_downloads_the_documents(self):
        self._upload(self.low)
        http_login(self, 'r1_admin@test.local')
        listing = self.url_open('/api/transfer/%s/documents' % self.low.id).json()
        self.assertEqual(len(listing['documents']), 1)
        download = self.url_open('/api/transfer/documents/%s' % listing['documents'][0]['id'])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, PDF)
        self.assertEqual(download.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', download.headers['Content-Disposition'])

    def test_an_officer_cannot_use_the_admin_download(self):
        self._upload(self.low)
        doc = self.env['ir.attachment'].search([('res_model', '=', 'transfer.approval.request'), ('res_id', '=', self.low.id)])
        http_login(self, self.fx.officers[0].x_employee_code)
        self.assertEqual(self.url_open('/api/transfer/documents/%s' % doc.id).status_code, 403)
