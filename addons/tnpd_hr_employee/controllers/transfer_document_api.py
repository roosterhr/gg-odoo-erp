# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Supporting documents on a transfer request
==========================================

An officer attaches documents (medical certificate, spouse employment proof, ...) to their own
request; admins view and download them in the request details.

Rules (system parameters in brackets):
- types: PDF, JPG, PNG, checked by extension and by the first bytes of the file
- size: at most 5 MB each (tnpd.transfer.doc_max_mb)
- count: at most 5 per request (tnpd.transfer.doc_max_files)
- only while the request is a draft or pending, and only by the officer it belongs to

Files are stored as private attachments and are served only by the routes below, with
``Content-Disposition: attachment`` and ``nosniff``, to the owner or to an admin.

Officer (Employee login):
  POST   /api/employee-portal/transfer/<id>/documents           multipart field "file"
  GET    /api/employee-portal/transfer/<id>/documents
  GET    /api/employee-portal/transfer/<id>/documents/<doc_id>  download
  DELETE /api/employee-portal/transfer/<id>/documents/<doc_id>
Admin:
  GET    /api/transfer/<id>/documents
  GET    /api/transfer/documents/<doc_id>                        download
"""

import base64
import json
import logging
import os

from odoo import http
from odoo.http import content_disposition, request

from ..scope import scoped

_logger = logging.getLogger(__name__)

# extension -> (content type, first bytes a real file of that type starts with)
ALLOWED = {
    'pdf': ('application/pdf', (b'%PDF-',)),
    'jpg': ('image/jpeg', (b'\xff\xd8\xff',)),
    'jpeg': ('image/jpeg', (b'\xff\xd8\xff',)),
    'png': ('image/png', (b'\x89PNG\r\n\x1a\n',)),
}
MAX_MB_PARAM = 'tnpd.transfer.doc_max_mb'
MAX_FILES_PARAM = 'tnpd.transfer.doc_max_files'
EDITABLE_STATES = ('draft', 'pending')


def _limits(env):
    params = env['ir.config_parameter'].sudo()
    return (float(params.get_param(MAX_MB_PARAM, '5') or 5),
            int(params.get_param(MAX_FILES_PARAM, '5') or 5))


def check_upload(env, filename, content, existing_count):
    """An error message if the file may not be attached, else None."""
    max_mb, max_files = _limits(env)
    ext = os.path.splitext(filename or '')[1].lower().lstrip('.')
    if ext not in ALLOWED:
        return 'Only PDF, JPG and PNG files are accepted.'
    if not content:
        return 'The file is empty.'
    if len(content) > max_mb * 1024 * 1024:
        return 'The file is too large (limit %g MB).' % max_mb
    if not any(content.startswith(magic) for magic in ALLOWED[ext][1]):
        return 'The file content does not match its type.'
    if existing_count >= max_files:
        return 'At most %d documents can be attached to a request.' % max_files
    return None


class TransferDocumentApiController(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')], status=status,
        )

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status)

    # ── who is asking ─────────────────────────────────────────────────────────

    def _employee(self):
        uid = request.session.uid
        if not uid:
            return None, self._err('Authentication required', 401)
        emp = request.env['hr.employee'].sudo().search([('user_id', '=', uid), ('active', '=', True)], limit=1)
        if not emp:
            return None, self._err('No employee record linked to this session', 401)
        return emp, None

    def _admin(self):
        uid = request.session.uid
        if not uid:
            return None, self._err('Authentication required', 401)
        user = request.env['res.users'].sudo().browse(uid)
        if user.share or not (user.has_group('base.group_system') or user.has_group('base.group_erp_manager')):
            return None, self._err('Admin privileges required', 403)
        return uid, None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _attachments(self, rec):
        return request.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'transfer.approval.request'), ('res_id', '=', rec.id),
            ('description', '=', 'transfer-support-document'),
        ], order='id')

    @staticmethod
    def _fmt(att):
        return {
            'id': att.id, 'name': att.name, 'size': att.file_size or 0,
            'mimetype': att.mimetype or '', 'uploaded_on': att.create_date,
        }

    def _download(self, att):
        ext = os.path.splitext(att.name or '')[1].lower().lstrip('.')
        content_type = ALLOWED.get(ext, ('application/octet-stream',))[0]
        return request.make_response(base64.b64decode(att.datas or b''), headers=[
            ('Content-Type', content_type),
            ('Content-Disposition', content_disposition(att.name)),
            ('X-Content-Type-Options', 'nosniff'),
            ('Cache-Control', 'private, no-store'),
        ])

    def _own_request(self, emp, request_id):
        rec = request.env['transfer.approval.request'].sudo().browse(request_id).exists()
        # the same answer for "not yours" and "does not exist"
        return rec if rec and rec.employee_id == emp else None

    # ── officer ───────────────────────────────────────────────────────────────

    @http.route('/api/employee-portal/transfer/<int:request_id>/documents', auth='none', type='http',
                methods=['GET', 'POST'], csrf=False, readonly=False)
    def officer_documents(self, request_id, **_kw):
        emp, err = self._employee()
        if err:
            return err
        rec = self._own_request(emp, request_id)
        if not rec:
            return self._err('Transfer request not found', 404)
        if request.httprequest.method == 'GET':
            return self._json({'success': True, 'documents': [self._fmt(a) for a in self._attachments(rec)]})

        if rec.state not in EDITABLE_STATES:
            return self._err('Documents can only be added while the request is open.')
        upload = request.httprequest.files.get('file')
        if not upload:
            return self._err('Choose a file to attach.')
        content = upload.read()
        filename = os.path.basename(upload.filename or '').replace('\x00', '')
        problem = check_upload(request.env, filename, content, len(self._attachments(rec)))
        if problem:
            return self._err(problem)
        att = request.env['ir.attachment'].sudo().create({
            'name': filename, 'datas': base64.b64encode(content), 'type': 'binary', 'public': False,
            'res_model': 'transfer.approval.request', 'res_id': rec.id,
            'description': 'transfer-support-document',
        })
        return self._json({'success': True, 'document': self._fmt(att)})

    @http.route('/api/employee-portal/transfer/<int:request_id>/documents/<int:doc_id>', auth='none',
                type='http', methods=['GET', 'DELETE'], csrf=False, readonly=False)
    def officer_document(self, request_id, doc_id, **_kw):
        emp, err = self._employee()
        if err:
            return err
        rec = self._own_request(emp, request_id)
        att = self._attachments(rec).filtered(lambda a: a.id == doc_id) if rec else None
        if not att:
            return self._err('Document not found', 404)
        if request.httprequest.method == 'DELETE':
            if rec.state not in EDITABLE_STATES:
                return self._err('Documents can only be removed while the request is open.')
            att.unlink()
            return self._json({'success': True})
        return self._download(att)

    # ── admin ─────────────────────────────────────────────────────────────────

    @http.route('/api/transfer/<int:request_id>/documents', auth='none', type='http', methods=['GET'], csrf=False)
    def admin_documents(self, request_id, **_kw):
        uid, err = self._admin()
        if err:
            return err
        rec = scoped(request.env, 'transfer.approval.request').search([('id', '=', request_id)])
        if not rec:
            return self._err('Transfer request not found', 404)
        return self._json({'success': True, 'documents': [self._fmt(a) for a in self._attachments(rec)]})

    @http.route('/api/transfer/documents/<int:doc_id>', auth='none', type='http', methods=['GET'], csrf=False)
    def admin_download(self, doc_id, **_kw):
        uid, err = self._admin()
        if err:
            return err
        att = request.env['ir.attachment'].sudo().search([
            ('id', '=', doc_id), ('res_model', '=', 'transfer.approval.request'),
            ('description', '=', 'transfer-support-document'),
        ])
        rec = scoped(request.env, 'transfer.approval.request').search([('id', '=', att.res_id)]) if att else None
        if not att or not rec:
            return self._err('Document not found', 404)
        return self._download(att)
