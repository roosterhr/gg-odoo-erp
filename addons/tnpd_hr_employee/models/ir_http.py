# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from werkzeug.exceptions import Forbidden

from odoo import models
from odoo.http import request

from .tnpd_scope_mixin import SCOPE_CTX

_READ_ONLY_METHODS = ('GET', 'HEAD', 'OPTIONS')
# What a Regional user may still change: their own password and profile.
_REGIONAL_MAY_WRITE = ('/api/change-password', '/api/auth/change-password', '/api/profile')


class IrHttp(models.AbstractModel):
    """
    The safety net under the per-endpoint scoping, for every /api/ route of every
    module (vacancy, institutions, ...):

    * a Regional session is view only: anything but a read is refused;
    * a Regional session's searches on employees, transfers, orders, institutions
      and vacancies are limited to its area.
    """

    _inherit = 'ir.http'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        uid = request.session.uid
        if not uid or not request.httprequest.path.startswith('/api/'):
            return
        users = request.env['res.users'].sudo()
        if users._tnpd_user_is_hq(users.browse(uid)):
            return
        if request.httprequest.method not in _READ_ONLY_METHODS \
                and request.httprequest.path.rstrip('/') not in _REGIONAL_MAY_WRITE:
            raise Forbidden('Only Headquarters can do this.')
        request.update_context(**{SCOPE_CTX: uid})
