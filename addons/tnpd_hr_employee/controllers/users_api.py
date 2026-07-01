# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Users REST API
==============

Admin-only endpoints for listing and managing system users (res.users).

Auth: all endpoints require a valid Odoo admin session (auth='none' + _require_auth + _is_admin).

Endpoints
---------
GET    /api/users                          — Paginated list with search / filter
GET    /api/users/<int:id>                 — Single user detail
PUT    /api/users/<int:id>                 — Update name / email / mobile / user_type
DELETE /api/users/<int:id>                 — Archive user (soft-delete, active=False)
POST   /api/users/<int:id>/reactivate      — Restore archived user (active=True)
POST   /api/users/invite                   — Generate invite token + send email
GET    /api/admin/invitations              — List all invitations with status
GET    /api/auth/verify-invite             — Validate invite token (public)
POST   /api/auth/signup                    — Complete account activation (public)
"""

import json
import logging
import os
import re
import secrets
import smtplib
import urllib.parse
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from odoo import http
from odoo.http import request

from .invite_email_template import INVITE_EMAIL_HTML, INVITE_EMAIL_SUBJECT

_EMAIL_RE  = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_MOBILE_RE = re.compile(r'^[+]?[\d\s\-()×]{7,20}$')

_logger    = logging.getLogger(__name__)
_MAX_LIMIT = 100

# ── Email Service ──────────────────────────────────────────────────────────────

def _send_invite_email(to_email, to_name, inviter_name, role, activation_link):
    """
    Send the invitation email via SMTP.
    Reads config from environment variables:
      SMTP_HOST, SMTP_PORT (default 587), SMTP_USERNAME, SMTP_PASSWORD,
      SMTP_FROM_EMAIL, SMTP_FROM_NAME (default 'TNPD Prison HRMS')
    Raises ValueError if SMTP_HOST is not set.
    Raises smtplib.SMTPException on send failure.
    """
    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    if not smtp_host:
        raise ValueError('SMTP_HOST environment variable is not configured.')

    smtp_port      = int(os.environ.get('SMTP_PORT', 587))
    smtp_user      = os.environ.get('SMTP_USERNAME', '').strip()
    smtp_pass      = os.environ.get('SMTP_PASSWORD', '').strip()
    from_email     = os.environ.get('SMTP_FROM_EMAIL', smtp_user).strip() or smtp_user
    from_name      = os.environ.get('SMTP_FROM_NAME', 'TNPD Prison HRMS').strip()

    html = INVITE_EMAIL_HTML
    html = html.replace('{{recipient_name}}',  to_name or to_email)
    html = html.replace('{{inviter_name}}',    inviter_name)
    html = html.replace('{{role}}',            role)
    html = html.replace('{{login_email}}',     to_email)
    html = html.replace('{{activation_link}}', activation_link)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = INVITE_EMAIL_SUBJECT
    msg['From']    = f'{from_name} <{from_email}>'
    msg['To']      = to_email
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            if smtp_port in (587, 25):
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email or smtp_user, [to_email], msg.as_string())
        _logger.info('Invite email sent to %s', to_email)
    except smtplib.SMTPAuthenticationError:
        _logger.error('SMTP auth failed for %s', smtp_user)
        raise
    except Exception as exc:
        _logger.error('SMTP send failed: %s', exc)
        raise


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fe_base_url(fallback='http://localhost:5173'):
    """Derive the frontend base URL from environment or Referer header."""
    app_url = os.environ.get('APP_URL', '').strip().rstrip('/')
    if app_url:
        return app_url
    referer = request.httprequest.headers.get('Referer', '')
    if referer:
        parsed = urllib.parse.urlparse(referer)
        return f'{parsed.scheme}://{parsed.netloc}'
    return fallback


def _invitation_status(payload):
    """Derive current status string from stored payload dict."""
    if payload.get('used'):
        return 'Accepted'
    expires = payload.get('expires', '')
    if expires:
        try:
            if datetime.utcnow() > datetime.fromisoformat(expires):
                return 'Expired'
        except Exception:
            pass
    return 'Pending'


def _format_invitation(key, payload):
    """Serialize a stored invitation payload to the API shape."""
    return {
        'token':       key.replace('tnpd.invite.', '', 1),
        'email':       payload.get('email', ''),
        'name':        payload.get('name', ''),
        'role':        (payload.get('user_type', 'admin') or 'admin').title(),
        'invited_by':  payload.get('invited_by_name', ''),
        'status':      _invitation_status(payload),
        'created_at':  payload.get('created_at', ''),
        'expires_at':  payload.get('expires', ''),
        'accepted_at': payload.get('accepted_at', ''),
        'email_sent':  payload.get('email_sent', False),
        'signup_url':  payload.get('signup_url', ''),
    }


# ── Controller ─────────────────────────────────────────────────────────────────

class UsersApiController(http.Controller):

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _ok(self, **data):
        return self._json({'success': True, **data})

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status=status)

    def _ensure_password_reset_table(self, cr):
        cr.execute("""
            CREATE TABLE IF NOT EXISTS tnpd_password_reset_required (
                user_id INTEGER PRIMARY KEY
            )
        """)

    def _require_auth(self):
        uid = request.session.uid
        if not uid:
            return None, self._json(
                {'success': False, 'message': 'Authentication required'}, status=401
            )
        return uid, None

    def _is_admin_user(self, user):
        try:
            return (
                user.has_group('base.group_system') or
                user.has_group('base.group_erp_manager')
            )
        except Exception:
            return False

    def _get_user_type(self, user):
        try:
            if user.has_group('base.group_system'):
                return 'Super Admin'
            if user.has_group('base.group_erp_manager'):
                return 'Admin'
        except Exception:
            pass
        return 'User'

    def _matches_user_type(self, user, user_type):
        utype = self._get_user_type(user)
        if user_type == 'super_admin':
            return utype == 'Super Admin'
        if user_type == 'admin':
            return utype in ('Admin', 'Super Admin')
        if user_type == 'user':
            return utype == 'User'
        return True

    def _format_user(self, user):
        emp = user.employee_ids[:1] if user.employee_ids else None

        institution      = ''
        institution_type = ''
        central_jail_id   = None
        central_jail_name = ''
        if emp:
            if emp.x_sub_jail_id:
                institution      = emp.x_sub_jail_id.name or ''
                institution_type = 'sub_jail'
            elif emp.x_district_jail_id:
                institution      = emp.x_district_jail_id.name or ''
                institution_type = 'district_jail'
            elif emp.x_central_jail_id:
                institution      = emp.x_central_jail_id.name or ''
                institution_type = 'central_jail'
            elif getattr(emp, 'x_sub_jail', None):
                institution      = emp.x_sub_jail or ''
                institution_type = 'sub_jail'
            elif getattr(emp, 'x_district_jail', None):
                institution      = emp.x_district_jail or ''
                institution_type = 'district_jail'
            elif getattr(emp, 'x_central_prison', None):
                institution      = emp.x_central_prison or ''
                institution_type = 'central_jail'

            if emp.x_central_jail_id:
                central_jail_id   = emp.x_central_jail_id.id
                central_jail_name = emp.x_central_jail_id.name or ''

        email  = ''
        mobile = ''
        if emp:
            email  = emp.work_email or user.email or ''
            mobile = getattr(emp, 'x_mobile_no', '') or ''
        else:
            email = user.email or ''

        return {
            'id':              user.id,
            'name':            user.name or '',
            'login':           user.login or '',
            'email':           email,
            'mobile':          mobile,
            'user_type':       self._get_user_type(user),
            'active':          user.active,
            'status':          'active' if user.active else 'inactive',
            'last_login':      str(user.login_date) if user.login_date else '',
            'create_date':     str(user.create_date) if user.create_date else '',
            'employee_db_id':  emp.id if emp else None,
            'employee_id':     (emp.x_employee_code or '') if emp else '',
            'designation':     (emp.x_designation or '') if emp else '',
            'institution':     institution,
            'institution_type': institution_type,
            'central_jail_id':   central_jail_id,
            'central_jail_name': central_jail_name,
        }

    # ── GET /api/users ────────────────────────────────────────────────────────

    @http.route('/api/users', auth='none', type='http', methods=['GET'], csrf=False)
    def list_users(self, **kwargs):
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            page   = max(1, int(kwargs.get('page', 1)))
            limit  = max(1, min(_MAX_LIMIT, int(kwargs.get('limit', 20))))
            offset = (page - 1) * limit

            q               = (kwargs.get('q')               or '').strip()
            user_type       = (kwargs.get('user_type')       or '').strip().lower()
            status          = (kwargs.get('status')          or 'active').strip().lower()
            central_jail_id = kwargs.get('central_jail_id')

            domain = [('share', '=', False)]

            if status == 'inactive':
                domain.append(('active', '=', False))
            elif status == 'active':
                domain.append(('active', '=', True))

            if q:
                domain += [
                    '|', ('name', 'ilike', q),
                    '|', ('login', 'ilike', q),
                         ('employee_ids.x_employee_code', 'ilike', q),
                ]

            if central_jail_id:
                try:
                    domain.append(('employee_ids.x_central_jail_id', '=', int(central_jail_id)))
                except (ValueError, TypeError):
                    pass

            Users   = request.env['res.users'].sudo().with_context(active_test=False)
            records = Users.search(domain, order='name asc')

            if user_type:
                records = records.filtered(lambda u: self._matches_user_type(u, user_type))

            total_count = len(records)
            paged       = records[offset: offset + limit]

            return self._json({
                'success':     True,
                'page':        page,
                'limit':       limit,
                'total_count': total_count,
                'users':       [self._format_user(u) for u in paged],
            })

        except Exception as exc:
            _logger.exception('GET /api/users failed: %s', exc)
            return self._err('Failed to load users.', status=500)

    # ── GET /api/users/<int:user_id> ──────────────────────────────────────────

    @http.route('/api/users/<int:user_id>', auth='none', type='http', methods=['GET'], csrf=False)
    def get_user(self, user_id, **kwargs):
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            user = request.env['res.users'].sudo().with_context(active_test=False).browse(user_id)
            if not user.exists() or user.share:
                return self._err('User not found.', status=404)
            return self._json({'success': True, 'user': self._format_user(user)})

        except Exception as exc:
            _logger.exception('GET /api/users/%s failed: %s', user_id, exc)
            return self._err('Failed to load user.', status=500)

    # ── PUT /api/users/<int:user_id> ──────────────────────────────────────────

    @http.route('/api/users/<int:user_id>', auth='none', type='http', methods=['PUT'], csrf=False)
    def update_user(self, user_id, **kwargs):
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            body = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return self._err('Invalid JSON body.')

        try:
            env  = request.env
            user = env['res.users'].sudo().with_context(active_test=False).browse(user_id)
            if not user.exists() or user.share:
                return self._err('User not found.', status=404)

            target_type      = self._get_user_type(user)
            is_current_super = current_user.has_group('base.group_system')
            if target_type == 'Super Admin' and not is_current_super:
                return self._err('Only a Super Admin can edit a Super Admin account.', status=403)

            name      = (body.get('name')      or '').strip()
            email     = (body.get('email')     or '').strip()
            mobile    = (body.get('mobile')    or '').strip()
            user_type = (body.get('user_type') or '').strip().lower()

            if not name:
                return self._err('Name is required.')
            if email and not _EMAIL_RE.match(email):
                return self._err('Invalid email address format.')
            if mobile and not _MOBILE_RE.match(mobile):
                return self._err('Invalid mobile number format.')
            if user_type and user_type not in ('admin', 'user'):
                return self._err('user_type must be "admin" or "user".')

            user_vals = {'name': name}
            if email:
                user_vals['email'] = email
            user.write(user_vals)

            emp = user.employee_ids[:1] if user.employee_ids else None
            if emp:
                emp_vals = {}
                if email:
                    emp_vals['work_email'] = email
                if mobile:
                    emp_vals['x_mobile_no'] = mobile
                if emp_vals:
                    emp.write(emp_vals)

            if user_type and target_type != 'Super Admin':
                group_erp = env.ref('base.group_erp_manager', raise_if_not_found=False)
                if group_erp:
                    if user_type == 'admin':
                        request.env.cr.execute(
                            "INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (group_erp.id, user.id),
                        )
                    elif user_type == 'user':
                        request.env.cr.execute(
                            "DELETE FROM res_groups_users_rel WHERE gid = %s AND uid = %s",
                            (group_erp.id, user.id),
                        )

            return self._json({'success': True, 'message': 'User updated successfully.', 'user': self._format_user(user)})

        except Exception as exc:
            _logger.exception('PUT /api/users/%s failed: %s', user_id, exc)
            return self._err('Failed to update user.', status=500)

    # ── DELETE /api/users/<int:user_id> ───────────────────────────────────────

    @http.route('/api/users/<int:user_id>', auth='none', type='http', methods=['DELETE'], csrf=False)
    def delete_user(self, user_id, **kwargs):
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        if user_id == uid:
            return self._err('You cannot delete your own account.', status=400)

        try:
            user = request.env['res.users'].sudo().with_context(active_test=False).browse(user_id)
            if not user.exists() or user.share:
                return self._err('User not found.', status=404)

            target_type      = self._get_user_type(user)
            is_current_super = current_user.has_group('base.group_system')
            if target_type == 'Super Admin' and not is_current_super:
                return self._err('Only a Super Admin can delete a Super Admin account.', status=403)

            user.write({'active': False})
            return self._json({'success': True, 'message': f'User "{user.name}" has been deactivated.'})

        except Exception as exc:
            _logger.exception('DELETE /api/users/%s failed: %s', user_id, exc)
            return self._err('Failed to delete user.', status=500)

    # ── POST /api/users/<int:user_id>/reactivate ──────────────────────────────

    @http.route('/api/users/<int:user_id>/reactivate', auth='none', type='http', methods=['POST'], csrf=False)
    def reactivate_user(self, user_id, **kwargs):
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            user = request.env['res.users'].sudo().with_context(active_test=False).browse(user_id)
            if not user.exists() or user.share:
                return self._err('User not found.', status=404)

            if user.active:
                return self._err('User is already active.', status=400)

            user.write({'active': True})
            return self._json({'success': True, 'message': f'User "{user.name}" has been reactivated.'})

        except Exception as exc:
            _logger.exception('POST /api/users/%s/reactivate failed: %s', user_id, exc)
            return self._err('Failed to reactivate user.', status=500)

    # ── POST /api/users/invite ────────────────────────────────────────────────

    @http.route('/api/users/invite', auth='none', type='http', methods=['POST'], csrf=False)
    def invite_user(self, **kwargs):
        """Admin generates an invite token, stores it, and emails the signup link."""
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            body = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return self._err('Invalid JSON body.')

        email     = (body.get('email')     or '').strip().lower()
        name      = (body.get('name')      or '').strip()
        user_type = (body.get('user_type') or 'admin').strip().lower()

        if not email:
            return self._err('email is required.')
        if not _EMAIL_RE.match(email):
            return self._err('Invalid email address format.')
        if user_type not in ('admin', 'user'):
            return self._err('user_type must be "admin" or "user".')

        # Block duplicate active invitations for same email
        ICP = request.env['ir.config_parameter'].sudo()
        existing_invites = ICP.search([('key', '=like', 'tnpd.invite.%')])
        for inv in existing_invites:
            try:
                p = json.loads(inv.value or '{}')
                if p.get('email', '').lower() == email and not p.get('used') and _invitation_status(p) == 'Pending':
                    return self._err('An active invitation already exists for this email. Revoke it first or wait for it to expire.')
            except Exception:
                pass

        # Check email not already a fully activated user (partial users without password are reusable)
        existing_user = request.env['res.users'].sudo().with_context(active_test=False).search(
            [('login', '=ilike', email)], limit=1)
        if existing_user and existing_user.password:
            return self._err('A user with this email already exists.')

        token   = secrets.token_urlsafe(32)
        now     = datetime.utcnow()
        expires = (now + timedelta(days=7)).isoformat()

        fe_base    = _fe_base_url()
        signup_url = f'{fe_base}/activate-account?token={token}'
        role_label = 'Admin' if user_type == 'admin' else 'User'

        payload = {
            'email':           email,
            'name':            name,
            'user_type':       user_type,
            'expires':         expires,
            'created_at':      now.isoformat(),
            'used':            False,
            'accepted_at':     '',
            'invited_by':      uid,
            'invited_by_name': current_user.name or '',
            'signup_url':      signup_url,
            'email_sent':      False,
        }

        ICP.set_param(f'tnpd.invite.{token}', json.dumps(payload))

        # Attempt to send email
        email_sent  = False
        email_error = ''
        try:
            _send_invite_email(
                to_email        = email,
                to_name         = name or email,
                inviter_name    = current_user.name or 'TNPD Admin',
                role            = role_label,
                activation_link = signup_url,
            )
            email_sent = True
            payload['email_sent'] = True
            ICP.set_param(f'tnpd.invite.{token}', json.dumps(payload))
        except ValueError as ve:
            email_error = str(ve)
            _logger.warning('Invite email skipped (SMTP not configured): %s', ve)
        except Exception as exc:
            email_error = 'Email delivery failed. Share the link manually.'
            _logger.error('Invite email send failed for %s: %s', email, exc)

        return self._ok(
            token      = token,
            signup_url = signup_url,
            expires    = expires,
            email      = email,
            email_sent = email_sent,
            email_error= email_error,
        )

    # ── GET /api/admin/invitations ────────────────────────────────────────────

    @http.route('/api/admin/invitations', auth='none', type='http', methods=['GET'], csrf=False)
    def list_invitations(self, **kwargs):
        """Return all invitations with their current status."""
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        try:
            ICP    = request.env['ir.config_parameter'].sudo()
            params = ICP.search([('key', '=like', 'tnpd.invite.%')], order='create_date desc')

            invitations = []
            for p in params:
                try:
                    payload = json.loads(p.value or '{}')
                    invitations.append(_format_invitation(p.key, payload))
                except Exception:
                    pass

            # Sort: Pending first, then by created_at desc
            order = {'Pending': 0, 'Accepted': 1, 'Expired': 2}
            invitations.sort(key=lambda x: (order.get(x['status'], 3), x.get('created_at', '').__class__.__name__))

            return self._ok(invitations=invitations, total=len(invitations))

        except Exception as exc:
            _logger.exception('GET /api/admin/invitations failed: %s', exc)
            return self._err('Failed to load invitations.', status=500)

    # ── DELETE /api/admin/invitations/<token> — revoke ────────────────────────

    @http.route('/api/admin/invitations/<string:token>', auth='none', type='http', methods=['DELETE'], csrf=False)
    def revoke_invitation(self, token, **kwargs):
        """Revoke (delete) a pending invitation."""
        uid, err = self._require_auth()
        if err:
            return err

        current_user = request.env['res.users'].sudo().browse(uid)
        if not self._is_admin_user(current_user):
            return self._err('Access denied. Admin privileges required.', status=403)

        ICP = request.env['ir.config_parameter'].sudo()
        key = f'tnpd.invite.{token}'
        rec = ICP.search([('key', '=', key)], limit=1)
        if not rec:
            return self._err('Invitation not found.', status=404)

        try:
            payload = json.loads(rec.value or '{}')
        except Exception:
            payload = {}

        if payload.get('used'):
            return self._err('Cannot revoke an already accepted invitation.', status=400)

        rec.unlink()
        return self._ok(message='Invitation revoked successfully.')

    # ── GET /api/auth/verify-invite ───────────────────────────────────────────

    @http.route('/api/auth/verify-invite', auth='none', type='http', methods=['GET'], csrf=False)
    def verify_invite(self, **kwargs):
        """Public — validate invite token and return pre-filled fields."""
        token = (kwargs.get('token') or '').strip()
        if not token:
            return self._err('token is required.', status=400)

        ICP = request.env['ir.config_parameter'].sudo()
        raw = ICP.get_param(f'tnpd.invite.{token}', default=None)
        if not raw:
            return self._err('Invalid or expired invite link.', status=404)

        try:
            payload = json.loads(raw)
        except Exception:
            return self._err('Invalid invite data.', status=400)

        if payload.get('used'):
            return self._err('This invite link has already been used.', status=410)

        expires = payload.get('expires', '')
        if expires:
            try:
                if datetime.utcnow() > datetime.fromisoformat(expires):
                    return self._err('This invite link has expired.', status=410)
            except Exception:
                pass

        return self._ok(
            valid     = True,
            email     = payload.get('email', ''),
            name      = payload.get('name', ''),
            user_type = payload.get('user_type', 'admin'),
        )

    # ── POST /api/auth/signup ─────────────────────────────────────────────────

    @http.route('/api/auth/signup', auth='none', type='http', methods=['POST'], csrf=False)
    def signup(self, **kwargs):
        """Public — complete account activation using a valid invite token."""
        try:
            body = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return self._err('Invalid JSON body.')

        token    = (body.get('token')    or '').strip()
        name     = (body.get('name')     or '').strip()
        password = (body.get('password') or '').strip()

        if not token:
            return self._err('token is required.')
        if not name:
            return self._err('Name is required.')
        if not password or len(password) < 8:
            return self._err('Password must be at least 8 characters.')

        ICP = request.env['ir.config_parameter'].sudo()
        raw = ICP.get_param(f'tnpd.invite.{token}', default=None)
        if not raw:
            return self._err('Invalid or expired invite link.', status=404)

        try:
            payload = json.loads(raw)
        except Exception:
            return self._err('Invalid invite data.', status=400)

        if payload.get('used'):
            return self._err('This invite link has already been used.', status=410)

        expires = payload.get('expires', '')
        if expires:
            try:
                if datetime.utcnow() > datetime.fromisoformat(expires):
                    return self._err('This invite link has expired.', status=410)
            except Exception:
                pass

        email     = (payload.get('email') or '').strip().lower()
        user_type = payload.get('user_type', 'admin')

        if not email:
            return self._err('Invite token is missing email.', status=400)

        existing = request.env['res.users'].sudo().with_context(active_test=False).search(
            [('login', '=ilike', email)], limit=1)
        if existing and existing.password:
            return self._err('A user with this email already exists.')

        try:
            from odoo import SUPERUSER_ID
            su_env    = request.env(user=SUPERUSER_ID)
            group_erp = su_env.ref('base.group_erp_manager', raise_if_not_found=False)
            main_company = su_env['res.company'].search([], limit=1, order='id asc')

            # Reuse a partial user left over from a previously failed signup
            partial = su_env['res.users'].with_context(active_test=False).search(
                [('login', '=ilike', email)], limit=1)
            if partial:
                partial.write({'name': name, 'active': True})
                new_user = partial
            else:
                user_vals = {
                    'name':        name,
                    'login':       email,
                    'email':       email,
                    'active':      True,
                    'share':       False,
                    'company_id':  main_company.id,
                    'company_ids': [(4, main_company.id)],
                }
                new_user = su_env['res.users'].with_context(no_reset_password=True).create(user_vals)

            if user_type == 'admin' and group_erp:
                request.env.cr.execute(
                    "INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (group_erp.id, new_user.id),
                )
            from passlib.context import CryptContext
            _crypt_ctx = CryptContext(schemes=['pbkdf2_sha512'], deprecated=['auto'])
            hashed = _crypt_ctx.hash(password)
            request.env.cr.execute(
                "UPDATE res_users SET password = %s WHERE id = %s",
                (hashed, new_user.id),
            )

            # Mark token consumed
            payload['used']        = True
            payload['accepted_at'] = datetime.utcnow().isoformat()
            ICP.set_param(f'tnpd.invite.{token}', json.dumps(payload))

            _logger.info(
                'Signup complete: user %s (id=%s, type=%s) created via invite token',
                email, new_user.id, user_type,
            )

            return self._ok(
                message   = 'Account created successfully. You can now log in.',
                user_id   = new_user.id,
                name      = new_user.name,
                email     = new_user.email,
                user_type = user_type,
            )

        except Exception as exc:
            _logger.exception('POST /api/auth/signup failed: %s', exc)
            return self._err('Failed to create account. Please try again.', status=500)

    # ── POST /api/auth/forgot-password ────────────────────────────────────────
    @http.route('/api/auth/forgot-password', auth='none', type='http', methods=['POST'], csrf=False)
    def forgot_password(self, **kwargs):
        """
        Generate a temporary password and email it if the identifier matches a registered account.

        Body: { "identifier": "<email or employee_id>", "login_type": "admin"|"employee" }
        Always returns success=true to avoid leaking whether an account exists.
        """
        try:
            raw = request.httprequest.get_data(as_text=True)
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._err('Invalid JSON body')

            identifier = str(data.get('identifier', '')).strip()
            login_type = str(data.get('login_type', 'admin')).strip()

            # For employee login, identifier may be absent (employeeId + email used instead)
            if not identifier and login_type != 'employee':
                return self._err('Please provide your email or Employee ID.')

            env = request.env

            # ── Locate the Odoo user ──────────────────────────────────────────
            user = None
            recipient_email = None
            recipient_name  = None

            if login_type == 'employee':
                employee_id_input = str(data.get('employeeId', '')).strip()
                email_input       = str(data.get('email', '')).strip()
                # Both fields required — find employee matching both employee code AND email
                if employee_id_input and email_input:
                    from odoo import SUPERUSER_ID
                    su_env = request.env(user=SUPERUSER_ID)
                    emp = su_env['hr.employee'].search([
                        ('x_employee_code', '=ilike', employee_id_input),
                        ('work_email', '=ilike', email_input),
                    ], limit=1)
                    if not emp:
                        # Fallback: raw SQL with trim to handle whitespace in stored values
                        request.env.cr.execute(
                            "SELECT id FROM hr_employee WHERE lower(trim(x_employee_code))=lower(%s) AND lower(trim(work_email))=lower(%s) LIMIT 1",
                            (employee_id_input, email_input)
                        )
                        row = request.env.cr.fetchone()
                        if row:
                            emp = su_env['hr.employee'].browse(row[0])
                else:
                    emp = env['hr.employee'].sudo().search([
                        '|', '|', '|',
                        ('work_email', '=ilike', identifier),
                        ('x_mobile_no', '=', identifier),
                        ('x_cug_mobile', '=', identifier),
                        ('x_employee_code', '=ilike', identifier),
                    ], limit=1)
                if emp:
                    # Use the direct employee→user link (login = employee code)
                    linked = emp.user_id
                    if not linked:
                        linked = env['res.users'].sudo().with_context(active_test=False).search([
                            ('login', '=ilike', emp.work_email),
                        ], limit=1)
                    if linked:
                        user = linked
                        recipient_email = emp.work_email
                        recipient_name  = emp.name
            else:
                # Admin: find by email first, then try mobile
                user = env['res.users'].sudo().with_context(active_test=False).search([
                    ('email', '=ilike', identifier),
                ], limit=1)
                if not user:
                    try:
                        user = env['res.users'].sudo().with_context(active_test=False).search([
                            ('partner_id.mobile', '=', identifier),
                        ], limit=1)
                    except Exception:
                        pass
                if user:
                    recipient_email = user.email or user.login
                    recipient_name  = user.name

            # ── Generate temp password & send email ───────────────────────────
            # Always return the same response to avoid account enumeration
            _GENERIC_OK = self._ok(message='If a matching account is found, a temporary password has been sent to the registered email address.')

            if not user or not recipient_email:
                _logger.info('forgot-password: no match for identifier=%s type=%s', identifier, login_type)
                return _GENERIC_OK

            # Generate an 8-char temp password: 2 upper + 4 digits + 2 special
            import random, string
            chars   = string.ascii_uppercase + string.digits
            temp_pw = (
                random.choice(string.ascii_uppercase) +
                random.choice(string.ascii_uppercase) +
                ''.join(random.choices(string.digits, k=4)) +
                random.choice('@#$!') +
                random.choice('@#$!')
            )
            # Shuffle so it doesn't always follow the same pattern
            temp_list = list(temp_pw)
            random.shuffle(temp_list)
            temp_pw = ''.join(temp_list)

            # Set the temp password via passlib SQL (ORM _set_password is broken in Odoo 19)
            from passlib.context import CryptContext
            _crypt_ctx = CryptContext(schemes=['pbkdf2_sha512'], deprecated=['auto'])
            hashed_temp = _crypt_ctx.hash(temp_pw)
            request.env.cr.execute(
                "UPDATE res_users SET password = %s WHERE id = %s",
                (hashed_temp, user.id),
            )
            # Flag user must change password on next login (no schema change needed)
            self._ensure_password_reset_table(request.env.cr)
            request.env.cr.execute(
                "INSERT INTO tnpd_password_reset_required (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user.id,),
            )
            _logger.info('forgot-password: temp password set for user id=%s | temp_pw=%s [REMOVE IN PROD]', user.id, temp_pw)

            # ── Build and send email ──────────────────────────────────────────
            smtp_host = os.environ.get('SMTP_HOST', '').strip()
            if not smtp_host:
                _logger.warning('forgot-password: SMTP_HOST not set, cannot send email')
                return _GENERIC_OK

            smtp_port  = int(os.environ.get('SMTP_PORT', 587))
            smtp_user  = os.environ.get('SMTP_USERNAME', '').strip()
            smtp_pass  = os.environ.get('SMTP_PASSWORD', '').strip()
            from_email = os.environ.get('SMTP_FROM_EMAIL', smtp_user).strip() or smtp_user
            from_name  = os.environ.get('SMTP_FROM_NAME', 'TNPD Prison HRMS').strip()

            # Build login-hint row — show username for admin, employee code for employee
            if login_type == 'employee':
                emp_code = getattr(user.employee_ids[:1], 'x_employee_code', '') if user.employee_ids else ''
                login_hint_label = 'Employee ID'
                login_hint_value = emp_code or user.login
            else:
                login_hint_label = 'Username'
                login_hint_value = user.login

            html_body = f"""
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:32px;background:#f8fafc;border-radius:10px;">
              <div style="text-align:center;margin-bottom:24px;">
                <div style="font-size:20px;font-weight:700;color:#0F172A;">TNPD HRMS</div>
                <div style="font-size:12px;color:#64748b;letter-spacing:.06em;text-transform:uppercase;margin-top:4px;">Tamil Nadu Prison Department</div>
              </div>
              <div style="background:white;border-radius:8px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,.06);">
                <p style="color:#334155;font-size:15px;margin-bottom:8px;">Hello <strong>{recipient_name or 'Officer'}</strong>,</p>
                <p style="color:#475569;font-size:14px;line-height:1.6;margin-bottom:20px;">
                  A password reset was requested for your TNPD HRMS account. Use the credentials below to log in.
                </p>
                <div style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:16px 24px;margin-bottom:20px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.08em;">{login_hint_label}</div>
                    <div style="font-size:15px;font-weight:700;color:#0F172A;font-family:monospace;">{login_hint_value}</div>
                  </div>
                  <div style="border-top:1px solid #e2e8f0;padding-top:12px;text-align:center;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Temporary Password</div>
                    <div style="font-size:26px;font-weight:700;color:#1D4ED8;letter-spacing:.12em;font-family:monospace;">{temp_pw}</div>
                  </div>
                </div>
                <p style="color:#64748b;font-size:13px;line-height:1.6;">
                  Please log in with these credentials and change your password immediately from the sidebar.<br/>
                  This temporary password is for one-time use.
                </p>
                <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;"/>
                <p style="color:#94a3b8;font-size:12px;">If you did not request this, please ignore this email. Your password will remain unchanged.</p>
              </div>
              <p style="text-align:center;color:#cbd5e1;font-size:11px;margin-top:16px;">© {datetime.utcnow().year} Tamil Nadu Prison Department</p>
            </div>
            """

            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'TNPD HRMS — Your Temporary Password'
            msg['From']    = f'{from_name} <{from_email}>'
            msg['To']      = recipient_email
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            try:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    if smtp_port in (587, 25):
                        server.starttls()
                        server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    server.sendmail(from_email or smtp_user, [recipient_email], msg.as_string())
                _logger.info('forgot-password: email sent to %s', recipient_email)
            except Exception as mail_exc:
                _logger.error('forgot-password: email send failed: %s', mail_exc)

            return _GENERIC_OK

        except Exception as exc:
            _logger.exception('POST /api/auth/forgot-password failed: %s', exc)
            return self._err('Internal server error', status=500)

    # ── POST /api/auth/change-password ────────────────────────────────────────
    @http.route('/api/auth/change-password', auth='none', type='http', methods=['POST'], csrf=False)
    def change_password(self, **kwargs):
        """
        Change the current user's password (requires active session).

        Body: { "current_password": "...", "new_password": "..." }
        """
        try:
            uid, err = self._require_auth()
            if err:
                return err

            raw = request.httprequest.get_data(as_text=True)
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._err('Invalid JSON body')

            current_pw = str(data.get('current_password', '')).strip()
            new_pw     = str(data.get('new_password', '')).strip()

            if not new_pw or len(new_pw) < 6:
                return self._err('New password must be at least 6 characters.')

            env  = request.env
            user = env['res.users'].sudo().browse(uid)

            # Check if this is a forced password change (came via forgot-password).
            # If so, skip current password verification — the user already proved identity by logging in.
            self._ensure_password_reset_table(request.env.cr)
            request.env.cr.execute(
                "SELECT 1 FROM tnpd_password_reset_required WHERE user_id = %s",
                (uid,),
            )
            is_forced_change = request.env.cr.fetchone() is not None

            if not is_forced_change:
                if not current_pw:
                    return self._err('Current password is required.')
                try:
                    env['res.users'].sudo()._check_credentials(current_pw, {'interactive': False})
                except Exception:
                    return self._err('Current password is incorrect.', status=401)

            # Set new password via passlib SQL (ORM _set_password is broken in Odoo 19)
            from passlib.context import CryptContext
            _crypt_ctx = CryptContext(schemes=['pbkdf2_sha512'], deprecated=['auto'])
            hashed_new = _crypt_ctx.hash(new_pw)
            request.env.cr.execute(
                "UPDATE res_users SET password = %s WHERE id = %s",
                (hashed_new, uid),
            )
            # Clear the force-change flag if it was set
            self._ensure_password_reset_table(request.env.cr)
            request.env.cr.execute(
                "DELETE FROM tnpd_password_reset_required WHERE user_id = %s",
                (uid,),
            )
            _logger.info('change-password: password changed for user id=%s', uid)
            return self._ok(message='Password changed successfully.')

        except Exception as exc:
            _logger.exception('POST /api/auth/change-password failed: %s', exc)
            return self._err('Internal server error', status=500)

    @http.route('/api/auth/me', auth='none', type='http', methods=['GET'], csrf=False)
    def me(self, **kwargs):
        """
        Returns current session user info including must_change_password flag.
        Frontend calls this after login to decide whether to show change-password prompt.
        """
        try:
            uid, err = self._require_auth()
            if err:
                return err

            env  = request.env
            user = env['res.users'].sudo().browse(uid)

            self._ensure_password_reset_table(request.env.cr)
            request.env.cr.execute(
                "SELECT 1 FROM tnpd_password_reset_required WHERE user_id = %s",
                (uid,),
            )
            must_change = request.env.cr.fetchone() is not None

            return self._ok(
                id=user.id,
                name=user.name,
                login=user.login,
                must_change_password=must_change,
            )

        except Exception as exc:
            _logger.exception('GET /api/auth/me failed: %s', exc)
            return self._err('Internal server error', status=500)
