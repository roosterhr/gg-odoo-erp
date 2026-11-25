# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import fields, models


class TnpdTransferReason(models.Model):
    """
    A reason an officer can give for a transfer request, with its weight.

    The weights are the priority scores (8 down to 1) agreed with the client. They are
    data, not code, so they can be changed under Settings > Technical without a release
    once the client confirms the final criteria. A request with several reasons combines
    their weights (see ``transfer.approval.request._combine_scores``).
    """

    _name = 'tnpd.transfer.reason'
    _description = 'Transfer Request Reason'
    _order = 'sequence, id'

    code = fields.Char(string='Code', required=True, index=True)
    name = fields.Char(string='Reason', required=True)
    score = fields.Integer(
        string='Weight', default=0,
        help='Priority weight of this reason. A request scores the sum of the weights of '
             'all the reasons chosen.',
    )
    sequence = fields.Integer(default=10)
    selectable = fields.Boolean(
        string='Can be chosen', default=True,
        help='Off for a retired reason: old requests keep showing it, new ones cannot pick it.',
    )
    active = fields.Boolean(default=True)

    _uniq_code = models.Constraint('UNIQUE (code)', 'A reason code must be unique.')
