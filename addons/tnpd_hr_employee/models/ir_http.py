# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from werkzeug.exceptions import Forbidden

from odoo import models
from odoo.http import request

_READ_ONLY_METHODS = ('GET', 'HEAD', 'OPTIONS')
# What a Regional user may still change: their own password and profile.
_REGIONAL_MAY_WRITE = ('/api/change-password', '/api/auth/change-password', '/api/profile')
# Outside /api/, a Regional session may only use the session calls of the app's own
# sign-in. Odoo's web client and RPC (/web/dataset/..., /odoo, ...) know nothing of the area.
_REGIONAL_WEB_PATHS = (
    '/web/session/authenticate', '/web/session/get_session_info',
    '/web/session/destroy', '/web/session/logout', '/web/login',
)


class IrHttp(models.AbstractModel):
    """
    The safety net under the per-endpoint scoping, for every route of every module
    (vacancy, institutions, ...):

    A Regional session is view only: anything but a read is refused. It also reaches
    nothing outside the app's own /api/ routes and sign-in (Odoo's web client and RPC
    are refused). Which screens a Regional user sees limited to their area is decided
    by the endpoints themselves (scope.scoped): personnel, transfers, orders and the
    dashboard. Everything else (analytics, vacancy, institutions) is the same for
    everyone.
    """

    _inherit = 'ir.http'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        uid = request.session.uid
        if not uid:
            return
        users = request.env['res.users'].sudo()
        if users._tnpd_user_is_hq(users.browse(uid)):
            return
        path = request.httprequest.path
        if not path.startswith('/api/'):
            if path.startswith(('/web/static/', '/web/assets/')) or path.rstrip('/') in _REGIONAL_WEB_PATHS:
                return
            raise Forbidden('Only Headquarters can do this.')
        if request.httprequest.method not in _READ_ONLY_METHODS \
                and path.rstrip('/') not in _REGIONAL_MAY_WRITE:
            raise Forbidden('Only Headquarters can do this.')
