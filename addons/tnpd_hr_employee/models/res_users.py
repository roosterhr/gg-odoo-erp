# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import fields, models


class ResUsers(models.Model):
    """
    Marks internal (non-portal) users that are allowed to use the Admin
    Login. Only the bootstrap admin and users who completed signup via a
    valid admin-issued invite token get this flag set to True.
    """

    _inherit = 'res.users'

    x_invited_by_admin = fields.Boolean(
        string='Invited by Admin',
        default=False,
        help='True only for the bootstrap admin or users created through '
             'the /api/auth/signup invite-acceptance flow. Gates access to '
             'the Admin Login — internal users without this flag (e.g. '
             'legacy or manually-created accounts) are rejected there.',
    )
