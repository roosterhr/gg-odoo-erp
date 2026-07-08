# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import logging
import re
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Tenure thresholds (days) used when prompting destination occupants to vacate.
_TENURE_DAYS_STANDARD = 1095  # 36 months
_TENURE_DAYS_HILL     = 547   # 18 months


class TransferApprovalRequest(models.Model):
    """
    Transfer Approval Request workflow model.

    Jail selection follows a strict three-tier cascade:
        Central Jail  →  District Jail  →  Sub Jail

    Onchange methods reset dependent fields when a parent changes; the
    ``_check_requested_jail_hierarchy`` constraint enforces the same rules
    at save time (covers API / batch writes that bypass onchange).

    State machine:
        draft     →  pending   (action_submit)
        pending   →  approved  (action_approve — updates employee jail posting)
        pending   →  rejected  (action_reject)
        pending   →  returned  (action_return — returned for correction)
        draft     →  cancelled (action_cancel)
        pending   →  cancelled (action_cancel)
    """

    _name = 'transfer.approval.request'
    _description = 'Transfer Approval Request'
    _rec_name = 'employee_id'
    _order = 'create_date desc'

    # ── Transfer classification ───────────────────────────────────────────────

    transfer_type = fields.Selection(
        selection=[
            ('request', 'Transfer Request'),
            ('tenure', 'Tenure Transfer'),
            ('admin_grounds', 'Administrative Grounds'),
        ],
        string='Transfer Type',
        required=True,
        default='request',
        index=True,
    )

    reason_category = fields.Selection(
        selection=[
            ('single_parent',   'Single Parent'),
            ('spouse_working',  'Spouse Working'),
            ('medical_reasons', 'Medical Reasons'),
            ('others',          'Others'),
        ],
        string='Reason Category',
        index=True,
    )

    transfer_reason = fields.Text(
        string='Transfer Reason',
        help='Reason provided by the employee for requesting the transfer.',
    )

    priority = fields.Selection(
        selection=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
        ],
        string='Priority',
        default='medium',
        required=False,
    )

    # ── Employee ──────────────────────────────────────────────────────────────

    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Employee',
        required=True,
        ondelete='restrict',
        index=True,
    )

    # ── Computed: tenure at current station ───────────────────────────────────

    tenure_years = fields.Float(
        string='Tenure at Current Station (Years)',
        compute='_compute_tenure_years',
        store=True,
        digits=(6, 2),
        help='Years since employee was posted to the current station.',
    )

    @api.depends('employee_id', 'employee_id.x_date_present_station')
    def _compute_tenure_years(self):
        today = date.today()
        for rec in self:
            posting_date = rec.employee_id.x_date_present_station if rec.employee_id else False
            if posting_date:
                rec.tenure_years = (today - posting_date).days / 365.25
            else:
                rec.tenure_years = 0.0

    # ── Current posting (read-only snapshot at request time) ──────────────────

    current_central_prison = fields.Many2one(
        comodel_name='prison.jail',
        string='Current Central Jail',
        domain=[('jail_type', '=', 'central_jail')],
        readonly=True,
    )
    current_district_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Current District Jail',
        domain=[('jail_type', '=', 'district_jail')],
        readonly=True,
    )
    current_sub_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Current Sub Jail',
        domain=[('jail_type', '=', 'sub_jail')],
        readonly=True,
    )

    # ── Requested transfer destination (cascading selection) ──────────────────

    requested_central_prison = fields.Many2one(
        comodel_name='prison.jail',
        string='Requested Central Jail',
        domain=[('jail_type', '=', 'central_jail'), ('active', '=', True)],
        required=True,
        index=True,
        ondelete='restrict',
    )
    requested_district_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Requested District Jail',
        # Optional: Central Prisons without District Jails (e.g. Vellore,
        # Tiruchirappalli) administer Sub Jails directly.
        domain=[('jail_type', '=', 'district_jail'), ('active', '=', True)],
        index=True,
        ondelete='restrict',
    )
    requested_sub_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Requested Sub Jail',
        domain=[('jail_type', '=', 'sub_jail'), ('active', '=', True)],
        index=True,
        ondelete='restrict',
    )

    # ── Preference 2 ─────────────────────────────────────────────────────────

    preference_2_central_prison = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 2 — Central Prison',
        domain=[('jail_type', '=', 'central_jail'), ('active', '=', True)],
        ondelete='restrict',
    )
    preference_2_district_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 2 — District Jail',
        domain=[('jail_type', '=', 'district_jail'), ('active', '=', True)],
        ondelete='restrict',
    )
    preference_2_sub_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 2 — Sub Jail',
        domain=[('jail_type', '=', 'sub_jail'), ('active', '=', True)],
        ondelete='restrict',
    )

    # ── Preference 3 ─────────────────────────────────────────────────────────

    preference_3_central_prison = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 3 — Central Prison',
        domain=[('jail_type', '=', 'central_jail'), ('active', '=', True)],
        ondelete='restrict',
    )
    preference_3_district_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 3 — District Jail',
        domain=[('jail_type', '=', 'district_jail'), ('active', '=', True)],
        ondelete='restrict',
    )
    preference_3_sub_jail = fields.Many2one(
        comodel_name='prison.jail',
        string='Preference 3 — Sub Jail',
        domain=[('jail_type', '=', 'sub_jail'), ('active', '=', True)],
        ondelete='restrict',
    )

    # ── Swap transfer ─────────────────────────────────────────────────────────

    swap_partner_id = fields.Many2one(
        comodel_name='transfer.approval.request',
        string='Swap Partner Request',
        ondelete='set null',
        index=True,
    )

    is_swap = fields.Boolean(
        string='Is Swap Transfer',
        default=False,
        index=True,
    )

    # ── Workflow ──────────────────────────────────────────────────────────────

    approval_user_id = fields.Many2one(
        comodel_name='res.users',
        string='Approval User',
        required=True,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
            ('returned', 'Returned'),
        ],
        string='State',
        default='draft',
        required=True,
        index=True,
    )
    requested_by = fields.Many2one(
        comodel_name='res.users',
        string='Requested By',
        default=lambda self: self.env.user,
        readonly=True,
    )
    approved_by = fields.Many2one(
        comodel_name='res.users',
        string='Actioned By',
        readonly=True,
    )
    approved_date = fields.Datetime(string='Action Date', readonly=True)
    remarks = fields.Text(string='Remarks')
    active = fields.Boolean(default=True)

    # ── Onchange: cascading reset ─────────────────────────────────────────────

    @api.onchange('requested_central_prison')
    def _onchange_requested_central_prison(self):
        """Reset district and sub when central changes; update both domains."""
        self.requested_district_jail = False
        self.requested_sub_jail = False
        if self.requested_central_prison:
            return {
                'domain': {
                    'requested_district_jail': [
                        ('jail_type', '=', 'district_jail'),
                        ('parent_id', '=', self.requested_central_prison.id),
                        ('active', '=', True),
                    ],
                    # Pre-filter sub jails by central; narrows further when DJ is chosen
                    'requested_sub_jail': [
                        ('jail_type', '=', 'sub_jail'),
                        ('parent_id', '=', self.requested_central_prison.id),
                        ('active', '=', True),
                    ],
                }
            }

    @api.onchange('requested_district_jail')
    def _onchange_requested_district_jail(self):
        """Reset sub when district changes; filter sub by DJ, or by Central if no DJ."""
        self.requested_sub_jail = False
        if self.requested_district_jail:
            return {
                'domain': {
                    'requested_sub_jail': [
                        ('jail_type', '=', 'sub_jail'),
                        ('parent_id', '=', self.requested_district_jail.id),
                        ('active', '=', True),
                    ]
                }
            }
        elif self.requested_central_prison:
            # DJ cleared or not applicable — show Sub Jails directly under Central
            return {
                'domain': {
                    'requested_sub_jail': [
                        ('jail_type', '=', 'sub_jail'),
                        ('parent_id', '=', self.requested_central_prison.id),
                        ('active', '=', True),
                    ]
                }
            }

    # ── Constraints: hierarchy integrity ─────────────────────────────────────

    @api.constrains(
        'requested_central_prison',
        'requested_district_jail',
        'requested_sub_jail',
    )
    def _check_requested_jail_hierarchy(self):
        """Ensure hierarchy is consistent: DJ belongs to Central; Sub belongs to DJ or Central."""
        for rec in self:
            if rec.requested_district_jail and rec.requested_central_prison:
                if rec.requested_district_jail.parent_id != rec.requested_central_prison:
                    raise ValidationError(
                        f'District Jail "{rec.requested_district_jail.name}" does not '
                        f'belong to Central Jail "{rec.requested_central_prison.name}".\n'
                        'Select a District Jail that falls under the chosen Central Jail.'
                    )
            if rec.requested_sub_jail:
                if rec.requested_district_jail:
                    # Sub Jail must be under the selected District Jail
                    if rec.requested_sub_jail.parent_id != rec.requested_district_jail:
                        raise ValidationError(
                            f'Sub Jail "{rec.requested_sub_jail.name}" does not belong to '
                            f'District Jail "{rec.requested_district_jail.name}".\n'
                            'Select a Sub Jail that falls under the chosen District Jail.'
                        )
                elif rec.requested_central_prison:
                    # No DJ: Sub Jail must be directly under the Central Prison
                    if rec.requested_sub_jail.parent_id != rec.requested_central_prison:
                        raise ValidationError(
                            f'Sub Jail "{rec.requested_sub_jail.name}" is not directly '
                            f'under Central Jail "{rec.requested_central_prison.name}".\n'
                            'Select a Sub Jail that belongs to the chosen Central Jail.'
                        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    @api.model
    def _current_prison_vals_from_employee(self, employee):
        """
        Build current_* field values from the employee's jail Many2one fields.
        Falls back to the legacy Char-field lookup if the Many2one fields are
        not yet populated (migration period).
        """
        # Prefer the new Many2one jail fields
        if employee.x_central_jail_id or employee.x_district_jail_id or employee.x_sub_jail_id:
            return {
                'current_central_prison': employee.x_central_jail_id.id or False,
                'current_district_jail':  employee.x_district_jail_id.id or False,
                'current_sub_jail':       employee.x_sub_jail_id.id or False,
            }

        # Legacy fallback: resolve the old Char fields against prison.jail
        return {
            'current_central_prison': self._lookup_jail('central_jail', employee.x_central_prison),
            'current_district_jail':  self._lookup_jail('district_jail', employee.x_district_jail),
            'current_sub_jail':       self._lookup_jail('sub_jail', employee.x_sub_jail),
        }

    # ── Swap detection ────────────────────────────────────────────────────────

    def _find_swap_partner(self):
        """
        Look for a reciprocal transfer request that forms a valid swap pair.

        A swap partner exists when:
          - Partner is currently posted at the prison THIS officer wants to go to
          - Partner wants to come to THIS officer's current prison
          - Both officers share the same designation (grade)
          - Partner request is pending and not already in a swap
        """
        self.ensure_one()
        my_grade    = self.employee_id.x_designation
        my_current  = (self.current_sub_jail or self.current_district_jail or self.current_central_prison)
        my_target   = (self.requested_sub_jail or self.requested_district_jail or self.requested_central_prison)

        if not my_grade or not my_current or not my_target:
            return self.env['transfer.approval.request']

        # Find pending requests where the employee is at my target prison
        # and they want to come to my current prison, same grade, no swap yet
        candidates = self.search([
            ('id',              '!=', self.id),
            ('state',           '=',  'pending'),
            ('is_swap',         '=',  False),
            ('swap_partner_id', '=',  False),
            ('active',          '=',  True),
        ])

        for c in candidates:
            c_grade   = c.employee_id.x_designation
            c_current = (c.current_sub_jail or c.current_district_jail or c.current_central_prison)
            c_target  = (c.requested_sub_jail or c.requested_district_jail or c.requested_central_prison)

            if (c_grade == my_grade
                    and c_current == my_target
                    and c_target == my_current):
                return c

        return self.env['transfer.approval.request']

    # ── Actions: Submit / Cancel / Return ────────────────────────────────────

    def action_submit(self):
        """Move draft → pending, then attempt swap detection."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Only draft requests can be submitted.')
        self.write({'state': 'pending'})

        # Auto-detect swap partner
        partner = self._find_swap_partner()
        if partner:
            self.write({'swap_partner_id': partner.id, 'is_swap': True})
            partner.write({'swap_partner_id': self.id, 'is_swap': True})
            _logger.info(
                'Swap pair detected: request %d ↔ request %d',
                self.id, partner.id,
            )

        # If the destination has no vacancy, prompt long-tenured occupants
        self.prompt_tenure_candidates_if_no_vacancy()

    # ── Tenure-vacancy prompting ──────────────────────────────────────────────

    def _resolve_role(self):
        """Requester's prison.role — via x_role_id, else designation name."""
        self.ensure_one()
        role = self.employee_id.x_role_id
        if role:
            return role
        designation = (self.employee_id.x_designation or '').strip()
        if not designation:
            return self.env['prison.role']
        base = re.sub(r'\s*\((?:women|men)\)\s*$', '', designation, flags=re.I).strip()
        return self.env['prison.role'].sudo().search([('name', '=ilike', base)], limit=1)

    def prompt_tenure_candidates_if_no_vacancy(self):
        """
        When this request targets a prison with NO vacancy for the requester's
        position, find officers at that prison holding the same position who
        have completed their tenure (3 years standard / 18 months hill station)
        and have no open transfer request of their own, and notify each of them
        to apply for their tenure transfer — so a post can be vacated.

        The same officers automatically appear in the admin's Tenure Transfer
        eligibility list (they exceed the threshold and have not applied);
        the notification is additionally flagged there via 'vacancy_prompted'.

        Never raises — failures are logged and ignored so request submission
        is never blocked by this convenience flow.
        """
        for rec in self:
            try:
                rec._prompt_tenure_candidates()
            except Exception:
                _logger.exception(
                    'tenure-vacancy prompt failed for transfer request %d', rec.id)

    def _prompt_tenure_candidates(self):
        self.ensure_one()
        dest = self._get_destination_prison()
        if not dest:
            return

        role = self._resolve_role()
        if not role:
            return

        desig_vac = self.env['prison.designation.vacancy'].sudo().search([
            ('prison_id', '=', dest.id),
            ('role_id', '=', role.id),
        ], limit=1)
        # Only act when we positively know there is no vacancy
        if not desig_vac or desig_vac.vacancy_count > 0:
            return

        threshold = _TENURE_DAYS_HILL if dest.is_hill_station else _TENURE_DAYS_STANDARD
        cutoff = date.today() - timedelta(days=threshold)

        occupants = self.env['hr.employee'].sudo().search([
            ('active', '=', True),
            ('x_employee_code', '!=', False),
            ('x_employee_code', '!=', ''),
            ('id', '!=', self.employee_id.id),
            ('x_date_present_station', '!=', False),
            ('x_date_present_station', '<=', str(cutoff)),
            '|', '|',
            ('x_central_jail_id', '=', dest.id),
            ('x_district_jail_id', '=', dest.id),
            ('x_sub_jail_id', '=', dest.id),
        ])

        # Same position: compare designation with the (Women)/(Men) suffix removed
        def _base(desig):
            return re.sub(r'\s*\((?:women|men)\)\s*$', '', (desig or '').strip(), flags=re.I).lower()

        role_base = role.name.strip().lower()
        occupants = occupants.filtered(lambda e: _base(e.x_designation) == role_base)
        if not occupants:
            return

        # Skip occupants who already have an open transfer request of any type
        open_reqs = self.env['transfer.approval.request'].sudo().search([
            ('employee_id', 'in', occupants.ids),
            ('state', 'in', ['draft', 'pending']),
            ('active', '=', True),
        ])
        occupants = occupants - open_reqs.mapped('employee_id')
        if not occupants:
            return

        Notification = self.env['tnpd.notification'].sudo()
        years_label = '18 months' if dest.is_hill_station else '3 years'
        for emp in occupants:
            # One live prompt per officer — do not spam on every new request
            existing = Notification.search([
                ('employee_id', '=', emp.id),
                ('action_type', '=', 'tenure_transfer_prompt'),
                ('is_read', '=', False),
            ], limit=1)
            if existing:
                continue
            Notification.create({
                'employee_id':         emp.id,
                'transfer_request_id': self.id,
                'notification_type':   'general',
                'action_type':         'tenure_transfer_prompt',
                'message': (
                    f'You have served more than {years_label} at {dest.name}. '
                    f'An incoming transfer request for your position is waiting '
                    f'for a vacancy. Please apply for your tenure transfer at '
                    f'the earliest.'
                ),
            })
            _logger.info(
                'Tenure prompt sent to employee %d (%s) at %s for request %d',
                emp.id, emp.x_employee_code, dest.name, self.id,
            )

    def action_cancel(self):
        """Move draft/pending → cancelled."""
        self.ensure_one()
        if self.state not in ('draft', 'pending'):
            raise UserError('Only draft or pending requests can be cancelled.')
        self.write({'state': 'cancelled'})

    def action_return(self):
        """Move pending → returned (for correction). Approver only."""
        self.ensure_one()
        if self.approval_user_id != self.env.user:
            raise UserError(
                'Only the designated approver (%s) can return this request.'
                % self.approval_user_id.name
            )
        if self.state != 'pending':
            raise UserError('Only pending requests can be returned for correction.')
        self.write({'state': 'returned'})

    # ── Actions: Approve / Reject ─────────────────────────────────────────

    def _send_transfer_notification(self, notification_type, message):
        """Create a tnpd.notification record for the linked employee."""
        try:
            self.env['tnpd.notification'].sudo().create({
                'employee_id':         self.employee_id.id,
                'transfer_request_id': self.id,
                'notification_type':   notification_type,
                'action_type':         notification_type,
                'message':             message,
                'sent_by':             self.env.user.id,
            })
        except Exception:
            pass  # Never let notification failure block the approval flow

    def _get_destination_prison(self):
        """Return the most-specific non-null destination prison record."""
        return (
            self.requested_sub_jail
            or self.requested_district_jail
            or self.requested_central_prison
            or False
        )

    def _validate_vacancy_and_gender(self):
        """
        Pre-approval guard:
        1. Destination prison must not be closed.
        2. If employee has a role (x_role_id), destination must have vacancy
           for that role.
        3. Women roles must go to Women hierarchy prisons; Men roles to General.
        """
        dest = self._get_destination_prison()
        if not dest:
            raise UserError('No destination prison selected.')

        if dest.is_closed:
            raise UserError(
                f'"{dest.name}" is marked as Closed and cannot be a transfer destination.'
            )

        role = self.employee_id.x_role_id
        if not role:
            return  # No role set — skip vacancy and gender checks

        # ── Gender / hierarchy check ──────────────────────────────────────────
        if role.gender_type == 'women' and dest.hierarchy_type != 'women':
            raise UserError(
                f'Role "{role.name}" is a Women role and can only be transferred to '
                f'a Women institution (SPW). "{dest.name}" is a General institution.'
            )
        if role.gender_type == 'men' and dest.hierarchy_type != 'general':
            raise UserError(
                f'Role "{role.name}" is a Men role and can only be transferred to '
                f'a General institution. "{dest.name}" is a Women institution.'
            )

        # ── Designation vacancy check ─────────────────────────────────────────
        desig = self.env['prison.designation.vacancy'].sudo().search([
            ('prison_id', '=', dest.id),
            ('role_id', '=', role.id),
        ], limit=1)

        if desig and not desig.is_vacancy_available():
            raise UserError(
                f'No vacancy available for "{role.name}" in "{dest.name}".\n'
                f'Sanctioned: {desig.sanctioned_strength}  '
                f'Filled: {desig.filled_strength}  '
                f'Vacancy: {desig.vacancy_count}.\n'
                'Transfer cannot be approved.'
            )

    def _adjust_vacancy_on_approve(self):
        """
        Update designation vacancy when transfer is approved:
            Source prison:      filled_strength -= 1
            Destination prison: filled_strength += 1
        """
        role = self.employee_id.x_role_id
        if not role:
            return

        dest = self._get_destination_prison()
        Desig = self.env['prison.designation.vacancy'].sudo()

        # Destination: +1 filled
        dest_desig = Desig.search([
            ('prison_id', '=', dest.id),
            ('role_id', '=', role.id),
        ], limit=1)
        if dest_desig:
            dest_desig.filled_strength = min(
                dest_desig.filled_strength + 1,
                dest_desig.sanctioned_strength,
            )

        # Source prison (most specific current posting)
        source = (
            self.employee_id.x_sub_jail_id
            or self.employee_id.x_district_jail_id
            or self.employee_id.x_central_jail_id
            or False
        )
        if source:
            src_desig = Desig.search([
                ('prison_id', '=', source.id),
                ('role_id', '=', role.id),
            ], limit=1)
            if src_desig and src_desig.filled_strength > 0:
                src_desig.filled_strength -= 1

        # Recompute prison-level aggregates
        Vacancy = self.env['prison.vacancy'].sudo()
        for pid in {dest.id, source.id if source else None} - {None}:
            pv = Vacancy.search([('prison_id', '=', pid)], limit=1)
            if pv:
                pv.recompute_from_designations()

    def _execute_approval(self, approved_by_user, now):
        """Apply the approval — update employee posting, set state, notify."""
        self.ensure_one()
        self.employee_id.write({
            'x_central_jail_id':  self.requested_central_prison.id or False,
            'x_district_jail_id': self.requested_district_jail.id or False,
            'x_sub_jail_id':      self.requested_sub_jail.id or False,
        })
        self._adjust_vacancy_on_approve()
        self.write({
            'state':         'approved',
            'approved_by':   approved_by_user.id,
            'approved_date': now,
        })
        to_jail = (
            self.requested_sub_jail.name
            or self.requested_district_jail.name
            or self.requested_central_prison.name
            or 'the requested posting'
        )
        self._send_transfer_notification(
            'transfer_approved',
            f'Your transfer request (Ref: TRF/{self.id}) has been approved. '
            f'You have been transferred to {to_jail}. '
            f'Approved by: {approved_by_user.name}.',
        )

    def action_approve(self):
        self.ensure_one()
        if self.approval_user_id != self.env.user:
            raise UserError(
                'Only the designated approver (%s) can approve this request.'
                % self.approval_user_id.name
            )
        if self.state != 'pending':
            raise UserError('Only pending requests can be approved.')

        self._validate_vacancy_and_gender()
        now = fields.Datetime.now()
        self._execute_approval(self.env.user, now)

        # Atomic swap: auto-approve the partner in the same transaction
        partner = self.swap_partner_id
        if partner and partner.state == 'pending':
            partner._execute_approval(self.env.user, now)
            _logger.info(
                'Swap auto-approval: request %d approved alongside request %d',
                partner.id, self.id,
            )

    def action_reject(self):
        self.ensure_one()
        if self.approval_user_id != self.env.user:
            raise UserError(
                'Only the designated approver (%s) can reject this request.'
                % self.approval_user_id.name
            )
        if self.state != 'pending':
            raise UserError('Only pending requests can be rejected.')
        self.write({
            'state': 'rejected',
            'approved_by': self.env.user.id,
            'approved_date': fields.Datetime.now(),
        })
        self._send_transfer_notification(
            'transfer_rejected',
            f'Your transfer request (Ref: TRF/{self.id}) has been rejected. '
            f'Please contact your administrator for more information.',
        )

    @api.model
    def _lookup_jail(self, jail_type, name):
        """
        Return the prison.jail id matching *name* and *jail_type*, or False.
        Used as a legacy bridge during the Char → Many2one migration period.
        """
        if not name:
            return False
        record = self.env['prison.jail'].search(
            [('name', '=', name), ('jail_type', '=', jail_type)],
            limit=1,
        )
        return record.id if record else False
