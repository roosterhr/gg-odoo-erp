# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# The order number is built from this pattern. {serial} is the next number of the
# sequence, {year} the year of the order and {section} the optional section code.
# Change them under Settings > Technical > System Parameters; nothing else to edit.
REPORT_NAME = 'tnpd_hr_employee.report_transfer_offer_letter'
NUMBER_FORMAT_PARAM = 'tnpd.transfer_order.number_format'
SECTION_CODE_PARAM = 'tnpd.transfer_order.section_code'
DEFAULT_NUMBER_FORMAT = '{serial}/{year}'


class TransferOrder(models.Model):
    """
    Proceedings of the Director General that orders one or more approved
    transfers (the "Transfer Offer Letter").

    Life cycle:
        draft     created when a transfer is approved; HQ checks the PDF and fills
                  in what only a person knows (Present, reference letter, DIG range)
        issued    numbered, locked, and every officer in it is notified
        cancelled kept for the record; its number is never reused

    One order covers one officer (single letter) or several officers of the same
    designation (bulk list). A transfer can be in one live order at a time.
    """

    _name = 'transfer.order'
    _inherit = ['tnpd.scope.mixin']
    _description = 'Transfer Order (Proceedings)'
    _order = 'id desc'

    # Fields that may change only while the order is still a draft.
    _DRAFT_ONLY_FIELDS = (
        'present', 'reference_letter', 'reference_date', 'dig_range',
        'order_date', 'transfer_ids',
    )

    name = fields.Char(
        string='Order No.', readonly=True, copy=False, index=True,
        help='Assigned when the order is issued.',
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('issued', 'Issued'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, index=True, copy=False,
    )
    transfer_ids = fields.Many2many(
        'transfer.approval.request', 'transfer_order_request_rel', 'order_id', 'request_id',
        string='Transfers',
    )
    employee_ids = fields.Many2many(
        'hr.employee', compute='_compute_employee_ids', string='Officers',
    )
    layout = fields.Selection(
        [('single', 'Single officer'), ('bulk', 'Bulk list')],
        compute='_compute_layout', string='Layout',
    )

    # What only a person knows; printed on the letter.
    present = fields.Char(string='Present')
    reference_letter = fields.Char(string='Reference letter no.')
    reference_date = fields.Date(string='Reference letter date')
    dig_range = fields.Char(string='DIG range', help='Range named in the "Copy to" line.')
    order_date = fields.Date(string='Dated', help='Date printed on the order. Set when issued.')

    issued_date = fields.Datetime(string='Issued on', readonly=True, copy=False)
    issued_by = fields.Many2one('res.users', string='Issued by', readonly=True, copy=False)

    # ── computed ──────────────────────────────────────────────────────────────

    @api.depends('transfer_ids.employee_id')
    def _compute_employee_ids(self):
        for order in self:
            order.employee_ids = order.transfer_ids.employee_id

    @api.depends('transfer_ids')
    def _compute_layout(self):
        for order in self:
            order.layout = 'bulk' if len(order.transfer_ids) > 1 else 'single'

    @api.depends('name', 'state')
    def _compute_display_name(self):
        for order in self:
            order.display_name = order.name or 'Draft order #%s' % (order.id or '')

    # ── constraints ───────────────────────────────────────────────────────────

    @api.constrains('transfer_ids', 'state')
    def _check_transfers(self):
        for order in self.filtered(lambda o: o.state != 'cancelled'):
            for transfer in order.transfer_ids:
                if transfer.state != 'approved':
                    raise ValidationError(
                        'Only approved transfers can be put in an order (TRF/%s is %s).'
                        % (transfer.id, transfer.state)
                    )
                clash = self.search([
                    ('id', '!=', order.id), ('state', '!=', 'cancelled'),
                    ('transfer_ids', '=', transfer.id),
                ], limit=1)
                if clash:
                    raise ValidationError(
                        'TRF/%s is already in transfer order %s.'
                        % (transfer.id, clash.display_name)
                    )
            designations = {self._designation(t) for t in order.transfer_ids}
            if len(designations) > 1:
                raise ValidationError(
                    'A bulk order must list officers of one designation; found: %s.'
                    % ', '.join(sorted(d or '(none)' for d in designations))
                )

    @api.model
    def _designation(self, transfer):
        employee = transfer.employee_id
        return employee.x_role_id.name or employee.x_designation or ''

    # ── creating ──────────────────────────────────────────────────────────────

    @api.model
    def _draft_for_transfers(self, transfers):
        """
        Draft order covering exactly these approved transfers.

        A single transfer that already has a live order gets that order back, so
        asking twice never makes a second one. Several transfers move into one new
        order; an old draft left empty by that is cancelled.
        """
        if not transfers:
            raise UserError('Choose at least one approved transfer.')
        if len(transfers) == 1:
            live = transfers._live_order()
            if live:
                return live
        busy = transfers.mapped('order_ids').filtered(lambda o: o.state == 'issued')
        if busy:
            raise UserError(
                'Already ordered in an issued order (%s). Cancel that order first.'
                % ', '.join(busy.mapped('display_name'))
            )
        previous = transfers.mapped('order_ids').filtered(lambda o: o.state == 'draft')
        if previous:
            previous.write({'transfer_ids': [(3, t.id) for t in transfers]})
            for order in previous.filtered(lambda o: not o.transfer_ids):
                order.write({'state': 'cancelled'})
        return self.create({'transfer_ids': [(6, 0, transfers.ids)]})

    # ── issue / cancel ────────────────────────────────────────────────────────

    @api.model
    def _next_number(self, when):
        """The order number for an order dated ``when``; the serial restarts every year."""
        serial = self.env['ir.sequence'].sudo().next_by_code('transfer.order', sequence_date=when) or '000'
        params = self.env['ir.config_parameter'].sudo()
        pattern = params.get_param(NUMBER_FORMAT_PARAM) or DEFAULT_NUMBER_FORMAT
        section = params.get_param(SECTION_CODE_PARAM) or ''
        number = (
            pattern.replace('{serial}', serial)
            .replace('{year}', str(when.year))
            .replace('{section}', section)
        )
        return number

    def action_issue(self):
        """Number the order, lock it and tell every officer in it."""
        today = fields.Date.context_today(self)
        for order in self:
            if order.state != 'draft':
                raise UserError('Only a draft order can be issued.')
            if not order.transfer_ids:
                raise UserError('The order has no transfers.')
            when = order.order_date or today
            order.write({
                'name': order._next_number(when),
                'state': 'issued',
                'order_date': when,
                'issued_date': fields.Datetime.now(),
                'issued_by': self.env.user.id,
            })
            order._freeze_pdf()
            order._notify_officers(
                'Your transfer order No. %s dated %s has been issued. '
                'You can download it from your transfers.'
                % (order.name, when.strftime('%d.%m.%Y'))
            )
        return True

    def _freeze_pdf(self):
        """
        Make and keep the PDF of an issued order.

        The report stores the file as an attachment the first time an issued order is
        printed and serves that file afterwards. Doing it here, at issue, fixes the
        letter as it was issued. If the PDF cannot be made the order is not issued.
        """
        report = self.env['ir.actions.report'].sudo().with_context(force_report_rendering=True)
        for order in self:
            report._render_qweb_pdf(REPORT_NAME, order.ids)
            if not self.env['ir.attachment'].sudo().search_count(
                [('res_model', '=', order._name), ('res_id', '=', order.id)]
            ):
                raise UserError('The PDF of the order could not be stored, so it was not issued.')

    def action_cancel(self):
        """Cancel a draft or issued order. An issued order's officers are told."""
        for order in self:
            if order.state == 'cancelled':
                continue
            was_issued = order.state == 'issued'
            order.write({'state': 'cancelled'})
            if was_issued:
                order._notify_officers(
                    'Transfer order No. %s has been cancelled. Please contact your '
                    'administrator for the new order.' % order.name
                )
        return True

    def action_print(self):
        """Report action for the Print button."""
        self.ensure_one()
        return self.env.ref('tnpd_hr_employee.action_report_transfer_offer_letter').report_action(self)

    def _notify_officers(self, message):
        Notification = self.env['tnpd.notification'].sudo()
        for order in self:
            for transfer in order.transfer_ids:
                try:
                    Notification.create({
                        'employee_id': transfer.employee_id.id,
                        'transfer_request_id': transfer.id,
                        'order_id': order.id,
                        'notification_type': 'transfer_order',
                        'action_type': 'transfer_order',
                        'message': message,
                        'sent_by': self.env.user.id,
                    })
                except Exception:  # a failed notice must never block the order
                    _logger.exception('Could not notify employee %s about order %s',
                                      transfer.employee_id.id, order.id)

    # ── protection ────────────────────────────────────────────────────────────

    def write(self, vals):
        if any(f in vals for f in self._DRAFT_ONLY_FIELDS) and any(
            o.state != 'draft' for o in self
        ):
            raise UserError(
                'An issued or cancelled order cannot be edited. Cancel it and make a new order.'
            )
        return super().write(vals)

    def unlink(self):
        if any(o.state != 'draft' for o in self):
            raise UserError('Issued and cancelled orders are kept for the record and cannot be deleted.')
        return super().unlink()

    def _tnpd_scope_domain(self, jail_ids):
        """Orders that carry at least one transfer inside the area."""
        requests = self.env['transfer.approval.request']._tnpd_scope_domain(jail_ids)
        return [('transfer_ids', 'any', requests)]
