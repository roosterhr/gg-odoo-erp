# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import models, fields


class TnpdNotification(models.Model):
    """
    Internal notification log for TNPD staff events.

    Notifications are generated programmatically (e.g. when an approver
    cannot approve a transfer because no vacancy exists) and stored here
    for future retrieval and display to the employee or admin.
    """
    _name        = 'tnpd.notification'
    _description = 'TNPD Staff Notification'
    _order       = 'sent_date desc, id desc'

    # ── Employee ────────────────────────────────────────────────────────────────
    employee_id   = fields.Many2one(
        'hr.employee',
        string='Employee',
        ondelete='cascade',
        index=True,
        help='The officer it is for, shown in the Employee login. Empty for an admin notice.',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Admin user',
        ondelete='cascade',
        index=True,
        help='The admin user it is for, shown in the Admin login (Superintendent, DIG, IG, DSP '
             'on a transfer order letter). Empty for an officer notice.',
    )
    employee_code = fields.Char(
        related='employee_id.x_employee_code',
        string='Employee Code',
        store=True,
        readonly=True,
    )
    employee_name = fields.Char(
        related='employee_id.name',
        string='Employee Name',
        store=True,
        readonly=True,
    )

    # ── Related record ──────────────────────────────────────────────────────────
    transfer_request_id = fields.Many2one(
        'transfer.approval.request',
        string='Transfer Request',
        ondelete='set null',
        index=True,
    )
    order_id = fields.Many2one(
        'transfer.order',
        string='Transfer Order',
        ondelete='set null',
        index=True,
    )

    # ── Notification content ────────────────────────────────────────────────────
    notification_type = fields.Selection(
        [
            ('no_vacancy',          'No Vacancy Available'),
            ('transfer_approved',   'Transfer Approved'),
            ('transfer_rejected',   'Transfer Rejected'),
            ('transfer_pending',    'Transfer Pending Review'),
            ('transfer_order',      'Transfer Order'),
            ('general',             'General'),
        ],
        string='Notification Type',
        default='general',
        required=True,
        index=True,
    )
    action_type = fields.Char(
        string='Action Type',
        help='Machine-readable action key, e.g. transfer_approval_no_vacancy',
    )
    message = fields.Text(string='Message', required=True)

    # ── Audit ───────────────────────────────────────────────────────────────────
    sent_by   = fields.Many2one(
        'res.users',
        string='Sent By',
        default=lambda self: self.env.uid,
        ondelete='set null',
        readonly=True,
    )
    sent_date = fields.Datetime(
        string='Sent Date',
        default=fields.Datetime.now,
        required=True,
        readonly=True,
        index=True,
    )

    # ── Read status ─────────────────────────────────────────────────────────────
    is_read    = fields.Boolean(string='Read', default=False)
    read_date  = fields.Datetime(string='Read Date', readonly=True)

    _recipient_check = models.Constraint(
        'CHECK (employee_id IS NOT NULL OR user_id IS NOT NULL)',
        'A notification needs an employee or an admin user.',
    )

    def mark_as_read(self):
        self.write({'is_read': True, 'read_date': fields.Datetime.now()})
