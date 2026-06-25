# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Dashboard REST API
==================

Aggregated statistics for the Admin Dashboard.

Auth: all endpoints require a valid Odoo session (auth='none' + _require_auth).

Endpoints
---------
GET /api/dashboard/summary           — Main KPIs (employees, transfers, users)
GET /api/dashboard/designation-stats — Headcount grouped by x_designation
GET /api/dashboard/jail-headcount    — Headcount grouped by jail facility
"""

import json
import logging
from datetime import date, timedelta

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Officers with more than this many years at the current posting are eligible
_TRANSFER_ELIGIBILITY_YEARS = 3


class DashboardApiController(http.Controller):

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _err(self, message, status=400):
        return self._json({'success': False, 'message': message}, status=status)

    def _require_auth(self):
        uid = request.session.uid
        if not uid:
            return None, self._json(
                {'success': False, 'message': 'Authentication required'}, status=401
            )
        return uid, None

    # ── GET /api/dashboard/summary ────────────────────────────────────────────

    @http.route('/api/dashboard/summary', methods=['GET'], auth='none', type='http', csrf=False)
    def dashboard_summary(self):
        uid, err = self._require_auth()
        if err:
            return err

        env = request.env(user=uid)
        Employee = env['hr.employee'].sudo()
        Transfer = env['transfer.approval.request'].sudo()
        User = env['res.users'].sudo()

        # Employee counts
        all_employees = Employee.search([('active', '=', True)])
        total_emp = len(all_employees)
        active_emp = len(all_employees.filtered(lambda e: e.x_status == 'active'))

        # Transfer counts
        cutoff = date.today() - timedelta(days=365)
        recent_transfers = Transfer.search([('create_date', '>=', cutoff.isoformat())])
        transfer_total    = len(recent_transfers)
        transfer_approved = len(recent_transfers.filtered(lambda t: t.state == 'approved'))
        transfer_pending  = len(recent_transfers.filtered(lambda t: t.state == 'pending'))
        transfer_rejected = len(recent_transfers.filtered(lambda t: t.state == 'rejected'))

        # Transfer eligibility (tenure >= 3 years at present station)
        eligible_cutoff = date.today() - timedelta(days=_TRANSFER_ELIGIBILITY_YEARS * 365)
        eligible_count = len(all_employees.filtered(
            lambda e: e.x_date_present_station and e.x_date_present_station <= eligible_cutoff
        ))

        # Average tenure at present station (in years, for eligible officers)
        tenure_employees = all_employees.filtered(
            lambda e: e.x_date_present_station
        )
        if tenure_employees:
            avg_days = sum(
                (date.today() - e.x_date_present_station).days
                for e in tenure_employees
            ) / len(tenure_employees)
            avg_tenure = f'{avg_days / 365:.1f} yrs'
        else:
            avg_tenure = '—'

        # Institutions — distinct jails from prison.jail master
        try:
            env.cr.execute('SAVEPOINT dashboard_jail')
            Jail = env['prison.jail'].sudo()
            central_jails  = Jail.search([('jail_type', '=', 'central_jail'), ('active', '=', True)])
            district_jails = Jail.search([('jail_type', '=', 'district_jail'),  ('active', '=', True)])
            sub_jails      = Jail.search([('jail_type', '=', 'sub_jail'),        ('active', '=', True)])
            total_institutions = len(central_jails) + len(district_jails) + len(sub_jails)
            prison_categories  = len({j.jail_type for j in (central_jails | district_jails | sub_jails)})
            institution_list   = [{'id': j.id, 'name': j.name} for j in (central_jails | district_jails)]
            env.cr.execute('RELEASE SAVEPOINT dashboard_jail')
        except Exception:
            env.cr.execute('ROLLBACK TO SAVEPOINT dashboard_jail')
            central_jails = district_jails = sub_jails = env['res.users'].sudo().browse([])
            total_institutions = 0
            prison_categories  = 0
            institution_list   = []

        # Admin users — fetch all internal users, filter by has_group in Python
        # (groups_id domain is unreliable in Odoo 19 — use has_group instead)
        all_internal = User.search([('share', '=', False), ('active', '=', True)])
        admin_users_all = all_internal.filtered(
            lambda u: u.has_group('base.group_system') or u.has_group('base.group_erp_manager')
        )

        admin_user_list = [
            {
                'id':          u.id,
                'name':        u.name,
                'email':       u.email or u.login,
                'active':      u.active,
                'last_login':  u.login_date.isoformat() if u.login_date else None,
                'institution': None,
                'status':      'Onboarded' if u.active else 'Inactive',
                'type':        'user',
            }
            for u in admin_users_all[:20]
        ]

        # Merge pending invitations into the list
        try:
            from datetime import datetime as _dt
            ICP = env['ir.config_parameter'].sudo()
            inv_params = ICP.search([('key', '=like', 'tnpd.invite.%')])
            for p in inv_params:
                try:
                    payload = __import__('json').loads(p.value or '{}')
                    # Only show pending (not used, not expired)
                    if payload.get('used'):
                        continue
                    expires = payload.get('expires', '')
                    if expires:
                        try:
                            if _dt.utcnow() > _dt.fromisoformat(expires):
                                continue
                        except Exception:
                            pass
                    admin_user_list.append({
                        'id':          None,
                        'name':        payload.get('name') or payload.get('email', ''),
                        'email':       payload.get('email', ''),
                        'active':      False,
                        'last_login':  None,
                        'institution': None,
                        'status':      'Pending',
                        'type':        'invite',
                        'invited_by':  payload.get('invited_by_name', ''),
                        'invited_at':  payload.get('created_at', ''),
                        'role':        (payload.get('user_type') or 'admin').title(),
                    })
                except Exception:
                    pass
        except Exception:
            pass

        # Recent 5 transfers — same serialization logic as list-all endpoint
        # (order='id desc' matches the Transfers page; fallback to live employee fields)
        recent_5 = Transfer.search([('active', '=', True)], order='id desc', limit=5)
        recent_list = []
        for t in recent_5:
            emp = t.employee_id

            # from_prison: snapshot fields first, fallback to live employee fields
            from_sub      = t.current_sub_jail.name      if t.current_sub_jail      else ''
            from_district = t.current_district_jail.name if t.current_district_jail else ''
            from_central  = t.current_central_prison.name if t.current_central_prison else ''
            from_jail = from_sub or from_district or from_central or ''
            if not from_jail and emp:
                from_jail = (
                    (emp.x_sub_jail_id.name      if emp.x_sub_jail_id      else '') or
                    (emp.x_district_jail_id.name if emp.x_district_jail_id else '') or
                    (emp.x_central_jail_id.name  if emp.x_central_jail_id  else '') or
                    getattr(emp, 'x_sub_jail', '') or
                    getattr(emp, 'x_district_jail', '') or
                    getattr(emp, 'x_central_prison', '') or '—'
                ).strip()
            if not from_jail:
                from_jail = '—'

            # to_prison: requested destination fields
            to_sub      = t.requested_sub_jail.name      if t.requested_sub_jail      else ''
            to_district = t.requested_district_jail.name if t.requested_district_jail else ''
            to_central  = t.requested_central_prison.name if t.requested_central_prison else ''
            to_jail = to_sub or to_district or to_central or '—'

            recent_list.append({
                'id':              t.id,
                'request_id':      t.id,
                'status':          t.state,
                'employee_name':   emp.name if emp else '—',
                'employee_code':   emp.x_employee_code if emp else '',
                'designation':     getattr(emp, 'x_designation', '') or '',
                'from_prison':     from_jail,
                'to_prison':       to_jail,
                'transfer_type':   getattr(t, 'transfer_type', '') or '',
                'created_at':      t.create_date.isoformat() if t.create_date else None,
                'approved_date':   t.approved_date.isoformat() if getattr(t, 'approved_date', None) else None,
            })

        # Recent audit — mail.message for transfer model as a lightweight proxy
        try:
            Message = env['mail.message'].sudo()
            audit_msgs = Message.search([
                ('model', 'in', ['transfer.approval.request', 'hr.employee']),
                ('message_type', '=', 'notification'),
            ], order='date desc', limit=10)
            recent_audit = [
                {
                    'actor':  msg.author_id.name if msg.author_id else 'System',
                    'action': msg.subject or (msg.body[:60] if msg.body else 'updated record'),
                    'entity': f'{msg.model} #{msg.res_id}',
                    'at':     msg.date.isoformat() if msg.date else None,
                }
                for msg in audit_msgs
            ]
        except Exception:
            recent_audit = []

        # Alert counts
        today = date.today()
        retirement_cutoff = today + timedelta(days=30)
        retiring_soon = len(all_employees.filtered(
            lambda e: e.x_date_of_retirement and today <= e.x_date_of_retirement <= retirement_cutoff
        ))

        return self._json({
            'total_employees':    total_emp,
            'active_employees':   active_emp,
            'total_institutions': total_institutions,
            'prison_categories':  prison_categories,
            'admin_users':        len(admin_users_all),
            'admin_user_list':    admin_user_list,
            'transfer_total':     transfer_total,
            'transfer_approved':  transfer_approved,
            'transfer_pending':   transfer_pending,
            'transfer_rejected':  transfer_rejected,
            'eligible_transfer':  eligible_count,
            'avg_tenure':         avg_tenure,
            'ai_flagged':         eligible_count,
            'retiring_soon':      retiring_soon,
            'open_grievances':    0,
            'pending_pr':         0,
            'recent_transfers':   recent_list,
            'recent_audit':       recent_audit,
            'institution_list':   institution_list,
        })

    # ── GET /api/dashboard/designation-stats ─────────────────────────────────

    @http.route('/api/dashboard/designation-stats', methods=['GET'], auth='none', type='http', csrf=False)
    def designation_stats(self):
        uid, err = self._require_auth()
        if err:
            return err

        env = request.env(user=uid)
        params = request.httprequest.args

        domain = [('active', '=', True)]
        if params.get('institution'):
            try:
                jid = int(params['institution'])
                domain += ['|', '|',
                           ('x_central_jail_id', '=', jid),
                           ('x_district_jail_id', '=', jid),
                           ('x_sub_jail_id', '=', jid)]
            except (ValueError, TypeError):
                pass
        if params.get('designation'):
            domain.append(('x_designation', '=', params['designation']))

        Employee = env['hr.employee'].sudo()
        employees = Employee.search(domain)

        # Group by designation
        by_desig = {}
        eligible_cutoff = date.today() - timedelta(days=_TRANSFER_ELIGIBILITY_YEARS * 365)
        for emp in employees:
            key = (emp.x_designation or 'Unassigned').strip()
            if key not in by_desig:
                by_desig[key] = {'count': 0, 'eligible': 0}
            by_desig[key]['count'] += 1
            if emp.x_date_present_station and emp.x_date_present_station <= eligible_cutoff:
                by_desig[key]['eligible'] += 1

        # Assign groups (simple heuristic — extend as needed)
        def _group(desig):
            d = desig.upper()
            if 'IPS' in d or 'IGP' in d or 'DGP' in d or 'ADGP' in d:
                return 'IPS'
            if 'SUPERINTENDENT' in d or 'JAILOR' in d or 'DEPUTY' in d:
                return 'Executive'
            return 'Subordinate'

        stats = sorted(
            [
                {
                    'designation':    k,
                    'group':          _group(k),
                    'count':          v['count'],
                    'eligible_count': v['eligible'],
                }
                for k, v in by_desig.items()
            ],
            key=lambda x: x['count'],
            reverse=True,
        )

        return self._json({'stats': stats})

    # ── GET /api/dashboard/jail-headcount ─────────────────────────────────────

    @http.route('/api/dashboard/jail-headcount', methods=['GET'], auth='none', type='http', csrf=False)
    def jail_headcount(self):
        uid, err = self._require_auth()
        if err:
            return err

        env = request.env(user=uid)
        Employee = env['hr.employee'].sudo()
        Jail = env['prison.jail'].sudo()

        all_jails = Jail.search([('active', '=', True)], order='name')
        jail_name_map = {j.name.strip().lower(): j for j in all_jails if j.name}

        counts = {}       # jail.id → count
        text_counts = {}  # jail name (text) → count for unmatched text-field employees

        for emp in Employee.search([('active', '=', True)]):
            jail = emp.x_sub_jail_id or emp.x_district_jail_id or emp.x_central_jail_id
            if jail:
                counts[jail.id] = counts.get(jail.id, 0) + 1
            else:
                # Fall back to text fields
                jail_text = None
                if emp.x_sub_jail and emp.x_sub_jail.strip().lower() not in ('', 'nil'):
                    jail_text = emp.x_sub_jail.strip()
                elif emp.x_district_jail and emp.x_district_jail.strip().lower() not in ('', 'nil'):
                    jail_text = emp.x_district_jail.strip()
                elif emp.x_central_prison and emp.x_central_prison.strip().lower() not in ('', 'nil'):
                    jail_text = emp.x_central_prison.strip()
                if jail_text:
                    matched = jail_name_map.get(jail_text.lower())
                    if matched:
                        counts[matched.id] = counts.get(matched.id, 0) + 1
                    else:
                        text_counts[jail_text] = text_counts.get(jail_text, 0) + 1

        jails = []
        for jail in all_jails:
            c = counts.get(jail.id, 0)
            if c:
                jails.append({'id': jail.id, 'name': jail.name, 'count': c})

        # Include text-only entries that didn't match a prison.jail record
        for name, c in text_counts.items():
            jails.append({'id': None, 'name': name, 'count': c})

        jails.sort(key=lambda x: x['count'], reverse=True)
        return self._json({'jails': jails[:20]})

    # ── GET /api/dashboard/jail-categories ────────────────────────────────────

    @http.route('/api/dashboard/jail-categories', methods=['GET'], auth='none', type='http', csrf=False)
    def jail_categories(self):
        uid, err = self._require_auth()
        if err:
            return err
        return self._json({
            'categories': [
                {'id': 'central_prison', 'name': 'Central Prison'},
                {'id': 'women_prison',   'name': "Women's Prison"},
                {'id': 'district_jail',  'name': 'District Jail'},
                {'id': 'sub_jail',       'name': 'Sub-Jail'},
            ]
        })

    # ── GET /api/dashboard/institutions ──────────────────────────────────────

    @http.route('/api/dashboard/institutions', methods=['GET'], auth='none', type='http', csrf=False)
    def institutions_by_category(self):
        uid, err = self._require_auth()
        if err:
            return err

        env = request.env(user=uid)
        category_id = request.httprequest.args.get('categoryId', '')
        try:
            Jail = env['prison.jail'].sudo()
            domain = [('active', '=', True)]
            if category_id == 'women_prison':
                # Women's prisons are central_jail with 'special' in name
                domain.append(('jail_type', '=', 'central_jail'))
                jails = Jail.search(domain, order='name').filtered(
                    lambda j: 'special' in (j.name or '').lower()
                )
            elif category_id == 'central_prison':
                # Regular central prisons — exclude women's prisons
                domain.append(('jail_type', '=', 'central_jail'))
                jails = Jail.search(domain, order='name').filtered(
                    lambda j: 'special' not in (j.name or '').lower()
                )
            elif category_id:
                domain.append(('jail_type', '=', category_id))
                jails = Jail.search(domain, order='name')
            else:
                jails = Jail.search(domain, order='name')
            return self._json({
                'institutions': [{'id': j.id, 'name': j.name} for j in jails]
            })
        except Exception:
            return self._json({'institutions': []})

    # ── GET /api/dashboard/jail-category-personnel ────────────────────────────

    @http.route('/api/dashboard/jail-category-personnel', methods=['GET'], auth='none', type='http', csrf=False)
    def jail_category_personnel(self):
        uid, err = self._require_auth()
        if err:
            return err

        env       = request.env(user=uid)
        params    = request.httprequest.args
        cat_filter  = params.get('categoryId', '')
        inst_filter = params.get('institutionId', '')
        status_filter = params.get('status', '')

        Employee = env['hr.employee'].sudo()

        emp_domain = [('active', '=', True)]
        _STATUS_MAP = {
            'active':      'active',
            'inactive':    'inactive',
            'on_transfer': 'on_transfer',
            'on_leave':    'on_leave',
        }
        if status_filter and status_filter in _STATUS_MAP:
            emp_domain.append(('x_status', '=', _STATUS_MAP[status_filter]))

        # If filtering by specific institution, narrow domain directly
        if inst_filter:
            try:
                iid = int(inst_filter)
                emp_domain += ['|', '|',
                               ('x_central_jail_id', '=', iid),
                               ('x_district_jail_id', '=', iid),
                               ('x_sub_jail_id',      '=', iid)]
            except (ValueError, TypeError):
                pass

        all_employees = Employee.search(emp_domain)

        # Infer category from which jail field is populated.
        # Women's Prisons share jail_type='central_jail' but have 'special' in name —
        # split them out as a distinct category.
        counters = {'central_prison': 0, 'women_prison': 0, 'district_jail': 0, 'sub_jail': 0}
        inst_id = None
        if inst_filter:
            try:
                inst_id = int(inst_filter)
            except (ValueError, TypeError):
                pass

        for emp in all_employees:
            jail_id = None
            if emp.x_sub_jail_id:
                jtype   = 'sub_jail'
                jail_id = emp.x_sub_jail_id.id
            elif emp.x_district_jail_id:
                jtype   = 'district_jail'
                jail_id = emp.x_district_jail_id.id
            elif emp.x_central_jail_id:
                jail_id = emp.x_central_jail_id.id
                jail_name = (emp.x_central_jail_id.name or '').lower()
                jtype = 'women_prison' if 'special' in jail_name else 'central_prison'
            # Fall back to text fields when ID fields are not populated
            elif emp.x_sub_jail and emp.x_sub_jail.strip() and emp.x_sub_jail.strip().lower() != 'nil':
                jtype = 'sub_jail'
            elif emp.x_district_jail and emp.x_district_jail.strip() and emp.x_district_jail.strip().lower() != 'nil':
                jtype = 'district_jail'
            elif emp.x_central_prison and emp.x_central_prison.strip() and emp.x_central_prison.strip().lower() != 'nil':
                jail_name = emp.x_central_prison.strip().lower()
                jtype = 'women_prison' if 'special' in jail_name else 'central_prison'
            else:
                continue  # no jail assigned — skip

            # Apply institution filter
            if inst_id and jail_id != inst_id:
                continue

            # Apply category filter
            if cat_filter and jtype != cat_filter:
                continue

            counters[jtype] += 1

        total = sum(counters.values())

        # Prison counts per category — women's prisons are central_jail with 'special' in name
        prison_counts = {'central_prison': 6, 'women_prison': 2, 'district_jail': 49, 'sub_jail': 119}
        try:
            Jail = env['prison.jail'].sudo()
            all_central = Jail.search([('jail_type', '=', 'central_jail'), ('active', '=', True)])
            women = all_central.filtered(lambda j: 'special' in (j.name or '').lower())
            prison_counts['central_prison'] = len(all_central) - len(women)
            prison_counts['women_prison']   = len(women)
            prison_counts['district_jail']  = Jail.search_count([('jail_type', '=', 'district_jail'), ('active', '=', True)])
            prison_counts['sub_jail']       = Jail.search_count([('jail_type', '=', 'sub_jail'),      ('active', '=', True)])
        except Exception:
            pass

        CATEGORY_META = [
            ('central_prison', 'Central Prison'),
            ('women_prison',   "Women's Prison"),
            ('district_jail',  'District Jail'),
            ('sub_jail',       'Sub-Jail'),
        ]
        categories = []
        for cid, cname in CATEGORY_META:
            cnt = counters[cid]
            pct = round((cnt / total) * 100, 1) if total > 0 else 0.0
            categories.append({
                'categoryId':   cid,
                'category':     cname,
                'prisonCount':  prison_counts.get(cid, 0),
                'officerCount': cnt,
                'percentage':   pct,
            })

        return self._json({'totalOfficers': total, 'categories': categories})

    # ── GET /api/dashboard/service-experience ─────────────────────────────────

    @http.route('/api/dashboard/service-experience', methods=['GET'], auth='none', type='http', csrf=False)
    def service_experience(self):
        uid, err = self._require_auth()
        if err:
            return err

        env = request.env(user=uid)
        Employee = env['hr.employee'].sudo()
        today = date.today()

        employees = Employee.search([('active', '=', True)])
        bands = {'0-5': 0, '6-10': 0, '11-20': 0, '20+': 0}

        for emp in employees:
            # Try several fields for date of joining / appointment
            doj = None
            for field in ('x_date_of_appointment', 'x_date_of_joining', 'x_date_appointment'):
                val = getattr(emp, field, None)
                if val:
                    doj = val
                    break
            if not doj:
                # Fall back to Odoo standard joining date field
                doj = getattr(emp, 'joining_date', None)
            if not doj:
                continue

            # doj may be a date or datetime string — normalise to date
            if hasattr(doj, 'date'):
                doj = doj.date()

            years = (today - doj).days / 365.25
            if years < 0:
                continue
            if years <= 5:
                bands['0-5'] += 1
            elif years <= 10:
                bands['6-10'] += 1
            elif years <= 20:
                bands['11-20'] += 1
            else:
                bands['20+'] += 1

        total = sum(bands.values())
        band_meta = [
            ('0-5',   '0–5 Years',   'Early career'),
            ('6-10',  '6–10 Years',  'Developing'),
            ('11-20', '11–20 Years', 'Experienced'),
            ('20+',   '20+ Years',   'Veteran'),
        ]
        result_bands = [
            {
                'key':   key,
                'label': label,
                'sub':   sub,
                'count': bands[key],
                'pct':   round(bands[key] / total * 100, 1) if total else 0.0,
            }
            for key, label, sub in band_meta
        ]

        return self._json({
            'total': total,
            'bands': result_bands,
        })
