# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, fields, models
from odoo.exceptions import AccessDenied, AccessError
from odoo.http import request


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

    # Who may change a login's area: Headquarters (or code running as superuser).
    _TNPD_SCOPE_FIELDS = ('x_access_scope', 'x_jurisdiction_jail_ids')

    # The only ways a Regional login may prove who it is: the app's own sign-in, and
    # re-checking the password when changing it. Odoo's /jsonrpc, /xmlrpc and API keys
    # are not among them, so those doors stay shut for a Regional login.
    _TNPD_REGIONAL_SIGN_IN = (
        '/web/session/authenticate', '/web/login',
        '/api/change-password', '/api/auth/change-password',
    )

    def _tnpd_check_may_change_scope(self, vals):
        if not self.env.su and any(f in vals for f in self._TNPD_SCOPE_FIELDS) \
                and not self._tnpd_user_is_hq(self.env.user):
            raise AccessError("Only Headquarters can change a login's access scope.")

    def write(self, vals):
        self._tnpd_check_may_change_scope(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._tnpd_check_may_change_scope(vals)
        return super().create(vals_list)

    def _check_credentials(self, credential, env):
        """
        A Regional login signs in through the app only. Without this, its own password
        would open Odoo's standard RPC (``/jsonrpc``, ``/xmlrpc``, API keys), which knows
        nothing about the area.

        A sign-in page is ``interactive``; RPC and API keys are not, and Odoo runs them
        without a web request attached, so the request cannot be used to tell them apart.
        The one non-interactive check allowed is the app's own change-password route.
        """
        interactive = bool((env or {}).get('interactive'))
        if not interactive and self.sudo().x_access_scope == 'regional' \
                and not self.sudo().has_group('base.group_system') \
                and not (request and request.httprequest.path.rstrip('/') in self._TNPD_REGIONAL_SIGN_IN):
            raise AccessDenied()
        return super()._check_credentials(credential, env)
