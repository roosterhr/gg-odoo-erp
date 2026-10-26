# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3
#
# Analytics API — real data from the database

import json
import logging
from datetime import date
from dateutil.relativedelta import relativedelta

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AnalyticsAPI(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _require_auth(self):
        uid = request.session.uid
        if not uid:
            return None, self._json({'success': False, 'message': 'Authentication required'}, status=401)
        return uid, None

    # ── GET /api/analytics/summary ────────────────────────────────────────────
    @http.route('/api/analytics/summary', auth='none', type='http', methods=['GET'], csrf=False)
    def summary(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            env = request.env(user=uid)
            emp_model = env['hr.employee'].sudo()
            tar_model = env['transfer.approval.request'].sudo()

            today      = date.today()
            year_start = date(today.year, 1, 1)
            prev_start = date(today.year - 1, 1, 1)
            prev_end   = date(today.year - 1, 12, 31)

            transfers_ytd  = tar_model.search_count([('create_date', '>=', str(year_start))])
            transfers_prev = tar_model.search_count([
                ('create_date', '>=', str(prev_start)),
                ('create_date', '<=', str(prev_end)),
            ])
            transfer_delta = round(((transfers_ytd - transfers_prev) / max(transfers_prev, 1)) * 100, 1) if transfers_prev else 0

            employees = emp_model.search([('active', '=', True), ('x_date_present_station', '!=', False)])
            tenures = []
            for e in employees:
                delta = (today - e.x_date_present_station).days / 365.25
                tenures.append(round(delta, 1))
            avg_tenure = round(sum(tenures) / len(tenures), 1) if tenures else 0

            total_employees = emp_model.search_count([
                ('active', '=', True),
                ('x_employee_code', '!=', False),
                ('x_employee_code', '!=', ''),
            ])

            retiring_soon = emp_model.search_count([
                ('active', '=', True),
                ('x_date_of_retirement', '>=', str(today)),
                ('x_date_of_retirement', '<=', str(today + relativedelta(years=5))),
            ])

            # Pending transfers count
            pending_transfers = tar_model.search_count([('state', '=', 'pending')])

            return self._json({
                'success': True,
                'transfers_ytd':    transfers_ytd,
                'transfer_delta':   transfer_delta,
                'avg_tenure':       avg_tenure,
                'total_employees':  total_employees,
                'retiring_soon':    retiring_soon,
                'pending_transfers': pending_transfers,
            })
        except Exception as exc:
            _logger.exception('GET /api/analytics/summary failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/transfers-by-month ────────────────────────────────
    @http.route('/api/analytics/transfers-by-month', auth='none', type='http', methods=['GET'], csrf=False)
    def transfers_by_month(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            period = kw.get('period', 'ytd')
            today  = date.today()

            if period == 'last_6_months':
                start    = (today - relativedelta(months=5)).replace(day=1)
                end_excl = (today + relativedelta(months=1)).replace(day=1)
            elif period == 'last_year':
                start    = date(today.year - 1, 1, 1)
                end_excl = date(today.year, 1, 1)
            else:  # ytd (default)
                start    = date(today.year, 1, 1)
                end_excl = (today + relativedelta(months=1)).replace(day=1)

            # Build ordered list of all months in range
            months_list = []
            cur = start
            while cur < end_excl:
                months_list.append(cur)
                cur = cur + relativedelta(months=1)

            month_keys   = [m.strftime('%Y-%m') for m in months_list]
            month_labels = [m.strftime('%b') for m in months_list]

            cr.execute("""
                SELECT TO_CHAR(create_date, 'YYYY-MM') AS mk, COUNT(*) AS cnt
                FROM transfer_approval_request
                WHERE create_date >= %s AND create_date < %s
                GROUP BY mk
            """, (str(start), str(end_excl)))
            data_map = {r[0]: r[1] for r in cr.fetchall()}

            data = [{'month': month_labels[i], 'value': data_map.get(mk, 0)}
                    for i, mk in enumerate(month_keys)]
            return self._json({'success': True, 'data': data})
        except Exception as exc:
            _logger.exception('GET /api/analytics/transfers-by-month failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/rank-distribution ─────────────────────────────────
    @http.route('/api/analytics/rank-distribution', auth='none', type='http', methods=['GET'], csrf=False)
    def rank_distribution(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            rank_type = kw.get('rank_type', 'all')

            rank_filter = ''
            if rank_type == 'officers':
                rank_filter = """
                    AND (x_designation ILIKE '%superintendent%'
                      OR x_designation ILIKE '%jailor%'
                      OR x_designation ILIKE '%officer%'
                      OR x_designation ILIKE '%inspector%')
                """
            elif rank_type == 'warders':
                rank_filter = """
                    AND (x_designation ILIKE '%warder%'
                      OR x_designation ILIKE '%guard%'
                      OR x_designation ILIKE '%constable%')
                """

            cr.execute(f"""
                SELECT x_designation, COUNT(*) as count
                FROM hr_employee
                WHERE active = true AND x_designation IS NOT NULL AND x_designation != ''
                  AND x_employee_code IS NOT NULL AND x_employee_code != ''
                {rank_filter}
                GROUP BY x_designation
                ORDER BY count DESC
                LIMIT 100
            """)
            rows = cr.fetchall()
            total = sum(r[1] for r in rows)
            data = [{'name': r[0], 'value': r[1]} for r in rows]
            return self._json({'success': True, 'data': data, 'total': total})
        except Exception as exc:
            _logger.exception('GET /api/analytics/rank-distribution failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/institution-headcount ─────────────────────────────
    @http.route('/api/analytics/institution-headcount', auth='none', type='http', methods=['GET'], csrf=False)
    def institution_headcount(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            inst_type = kw.get('inst_type', 'all')

            inst_filter = ''
            if inst_type == 'central':
                inst_filter = "AND (pj.name ILIKE '%central prison%' OR e.x_central_prison ILIKE '%central%')"
            elif inst_type == 'district':
                inst_filter = "AND (pj.name ILIKE '%district jail%' OR e.x_district_jail ILIKE '%district%')"
            elif inst_type == 'sub':
                inst_filter = "AND (pj.name ILIKE '%sub jail%' OR e.x_sub_jail ILIKE '%sub%')"

            cr.execute(f"""
                SELECT
                    COALESCE(
                        NULLIF(TRIM(e.x_central_prison), 'Nil'),
                        NULLIF(TRIM(e.x_district_jail),  'Nil'),
                        'Unknown'
                    ) AS institution,
                    COUNT(*) AS count
                FROM hr_employee e
                WHERE e.active = true
                  AND e.x_employee_code IS NOT NULL
                  AND e.x_employee_code != ''
                  {inst_filter}
                GROUP BY institution
                HAVING COALESCE(
                    NULLIF(TRIM(e.x_central_prison), 'Nil'),
                    NULLIF(TRIM(e.x_district_jail),  'Nil'),
                    'Unknown'
                ) != 'Unknown'
                ORDER BY count DESC
                LIMIT 100
            """)
            rows = cr.fetchall()
            data = [{'name': r[0], 'count': r[1]} for r in rows]
            return self._json({'success': True, 'data': data})
        except Exception as exc:
            _logger.exception('GET /api/analytics/institution-headcount failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/retirement-forecast ───────────────────────────────
    @http.route('/api/analytics/retirement-forecast', auth='none', type='http', methods=['GET'], csrf=False)
    def retirement_forecast(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            today = date.today()
            data = []
            for i in range(6):
                year_start = date(today.year + i, 1, 1)
                year_end   = date(today.year + i, 12, 31)
                cr.execute("""
                    SELECT COUNT(*) FROM hr_employee
                    WHERE active = true
                    AND x_date_of_retirement >= %s
                    AND x_date_of_retirement <= %s
                """, (str(year_start), str(year_end)))
                count = cr.fetchone()[0]
                data.append({'year': str(today.year + i), 'exits': count})
            return self._json({'success': True, 'data': data})
        except Exception as exc:
            _logger.exception('GET /api/analytics/retirement-forecast failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/vacancy-trend ─────────────────────────────────────
    @http.route('/api/analytics/vacancy-trend', auth='none', type='http', methods=['GET'], csrf=False)
    def vacancy_trend(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            # Show vacancy by institution (current snapshot) — more useful than sparse quarterly trend
            cr.execute("""
                SELECT
                    prison_name AS institution,
                    prison_type,
                    SUM(vacancy_count) AS vacant,
                    SUM(sanctioned_strength) AS sanctioned
                FROM prison_vacancy
                WHERE active = true
                GROUP BY prison_name, prison_type
                HAVING SUM(vacancy_count) > 0
                ORDER BY
                    CASE prison_type
                        WHEN 'central_prison'       THEN 1
                        WHEN 'special_prison_women' THEN 2
                        WHEN 'district_jail'        THEN 3
                        WHEN 'sub_jail'             THEN 4
                        ELSE 5
                    END,
                    vacant DESC
                LIMIT 100
            """)
            rows = cr.fetchall()

            TYPE_LABEL = {
                'central_prison':       'Central Prison',
                'special_prison_women': 'Special Prison',
                'district_jail':        'District Jail',
                'sub_jail':             'Sub Jail',
            }
            data = [
                {
                    'institution': r[0],
                    'type': TYPE_LABEL.get(r[1], r[1] or 'Other'),
                    'vacant': int(r[2] or 0),
                    'sanctioned': int(r[3] or 0),
                }
                for r in rows
            ]

            cr.execute("SELECT SUM(vacancy_count), SUM(sanctioned_strength) FROM prison_vacancy WHERE active = true")
            totals = cr.fetchone()
            total_vacant     = int(totals[0] or 0)
            total_sanctioned = int(totals[1] or 0)

            return self._json({
                'success': True,
                'data': data,
                'total_vacant': total_vacant,
                'total_sanctioned': total_sanctioned,
            })
        except Exception as exc:
            _logger.exception('GET /api/analytics/vacancy-trend failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)

    # ── GET /api/analytics/transfer-activity-by-district ─────────────────────
    @http.route('/api/analytics/transfer-activity-by-district', auth='none', type='http', methods=['GET'], csrf=False)
    def transfer_activity_by_district(self, **kw):
        uid, err = self._require_auth()
        if err:
            return err
        try:
            cr = request.env.cr
            today = date.today()

            last_6     = [(today - relativedelta(months=i)) for i in range(5, -1, -1)]
            months     = [m.strftime('%b') for m in last_6]
            month_keys = [m.strftime('%Y-%m') for m in last_6]

            cr.execute("""
                SELECT
                    COALESCE(MAX(pj.name), MAX(emp.x_district_jail), MAX(emp.x_central_prison), 'Unknown') AS district,
                    TO_CHAR(tar.create_date, 'YYYY-MM') AS month_key,
                    COUNT(*) AS transfers
                FROM transfer_approval_request tar
                JOIN hr_employee emp ON emp.id = tar.employee_id
                LEFT JOIN prison_jail pj ON pj.id = emp.x_district_jail_id
                WHERE tar.create_date >= NOW() - INTERVAL '6 months'
                  AND emp.x_employee_code IS NOT NULL AND emp.x_employee_code != ''
                GROUP BY month_key, emp.x_district_jail_id, emp.x_district_jail, emp.x_central_prison
                ORDER BY district, month_key
            """)
            rows = cr.fetchall()

            dist_map = {}
            for district, mk, count in rows:
                if district and district != 'Unknown':
                    if district not in dist_map:
                        dist_map[district] = {}
                    dist_map[district][mk] = count

            # Sort by total transfers descending, keep top 10
            sorted_dists = sorted(dist_map.keys(), key=lambda d: sum(dist_map[d].values()), reverse=True)

            data = [
                {'district': d, 'values': [dist_map[d].get(mk, 0) for mk in month_keys]}
                for d in sorted_dists[:10]
            ]

            # Trim leading months that are all-zero across every district
            if data:
                first_active = 0
                for col_i in range(len(month_keys)):
                    if any(row['values'][col_i] > 0 for row in data):
                        first_active = col_i
                        break
                months     = months[first_active:]
                month_keys = month_keys[first_active:]
                for row in data:
                    row['values'] = row['values'][first_active:]

            return self._json({'success': True, 'months': months, 'data': data})
        except Exception as exc:
            _logger.exception('GET /api/analytics/transfer-activity-by-district failed: %s', exc)
            return self._json({'success': False, 'message': str(exc)}, status=500)
