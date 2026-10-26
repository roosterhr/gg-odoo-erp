# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Who may see what, in one place.

    Employee = scoped(request.env, 'hr.employee')       # reads limited to the caller's area
    err = require_hq(uid)                               # for anything that changes data

Headquarters (and Odoo system admins) are never limited; Regional users see only
their institutions and everything under them, and cannot act.
"""

import json

from werkzeug.exceptions import Forbidden

from odoo.http import request

from .models.tnpd_scope_mixin import SCOPE_CTX


def scoped(env, model, uid=None):
    """
    ``env[model]`` as sudo, but every search limited to the caller's area.

    Fails closed: with no signed-in user there is no area to apply, so it refuses
    rather than quietly returning everything. Public endpoints must not call it.
    """
    if not uid and request:
        uid = request.session.uid
    if not uid:
        raise Forbidden('Authentication required.')
    return env[model].sudo().with_context(**{SCOPE_CTX: uid})


def scoped_jails(env, uid=None):
    """``prison.jail`` limited to the caller's area. Only the dashboard uses this; every
    other institution list is the same for everyone."""
    return scoped(env, 'prison.jail', uid).with_context(tnpd_scope_jails=True)


def is_hq(uid):
    """True if the user ``uid`` has Headquarters access (Odoo system admins always do)."""
    return request.env['res.users']._tnpd_user_is_hq(request.env['res.users'].sudo().browse(uid))


def require_hq(uid):
    """
    None for Headquarters, otherwise a 403 response. With no signed-in user it also
    returns None: the handler's own sign-in check then answers 401, as it always did.
    """
    if not uid or is_hq(uid):
        return None
    return request.make_response(
        json.dumps({'success': False, 'message': 'Only Headquarters can do this.'}),
        headers=[('Content-Type', 'application/json')], status=403,
    )
