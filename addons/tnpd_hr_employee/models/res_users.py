# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, fields, models


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

    x_access_scope = fields.Selection(
        selection=[('hq', 'Headquarters'), ('regional', 'Regional')],
        string='Access Scope',
        default='hq',
        required=True,
        help='Headquarters: sees everything and is the only scope that can act '
             '(transfers, orders, logins). Regional: view only, and only the '
             'institutions listed below and everything under them.',
    )
    x_jurisdiction_jail_ids = fields.Many2many(
        comodel_name='prison.jail',
        relation='res_users_jurisdiction_jail_rel',
        column1='user_id',
        column2='jail_id',
        string='Jurisdiction',
        help='Regional users only: the institutions this user oversees. Their '
             'district jails and sub-jails come with them.',
    )

    @api.model
    def _tnpd_user_is_hq(self, user):
        """True if ``user`` has Headquarters access (Odoo system admins always do)."""
        user = user.sudo()
        return user.has_group('base.group_system') or user.x_access_scope != 'regional'

    def _tnpd_scope_jail_ids(self):
        """
        None for Headquarters (no limit), otherwise the ids of the user's
        institutions and of every institution below them.

        Walks ``parent_id`` level by level, not ``child_of``: ``parent_path`` is
        empty on some institutions (earlier raw-SQL rebuild).
        """
        self.ensure_one()
        if self._tnpd_user_is_hq(self):
            return None
        Jail = self.env['prison.jail'].sudo().with_context(active_test=False)
        found = Jail.browse(self.sudo().x_jurisdiction_jail_ids.ids)
        level = found
        while level:
            level = Jail.search([('parent_id', 'in', level.ids)]) - found
            found |= level
        return found.ids
