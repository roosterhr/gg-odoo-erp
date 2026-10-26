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

from odoo.http import request

from .models.tnpd_scope_mixin import SCOPE_CTX


def scoped(env, model, uid=None):
    """``env[model]`` as sudo, but every search limited to the caller's area."""
    uid = uid or request.session.uid
    return env[model].sudo().with_context(**{SCOPE_CTX: uid})


def is_hq(uid):
    return request.env['res.users']._tnpd_user_is_hq(request.env['res.users'].sudo().browse(uid))


def require_hq(uid):
    """None for Headquarters, otherwise a 403 response."""
    if is_hq(uid):
        return None
    return request.make_response(
        json.dumps({'success': False, 'message': 'Only Headquarters can do this.'}),
        headers=[('Content-Type', 'application/json')], status=403,
    )


def scope_ids(uid, model):
    """None for Headquarters, otherwise the ids of ``model`` records inside the caller's area.
    For raw SQL, which the model filter cannot reach: ``AND id = ANY(%s)``."""
    if is_hq(uid):
        return None
    return scoped(request.env, model, uid).with_context(active_test=False).search([]).ids


def scope_jail_names(uid):
    """None for Headquarters, otherwise the names of the caller's institutions
    (for tables that record a posting by name, such as vacancies)."""
    if is_hq(uid):
        return None
    return scoped(request.env, 'prison.jail', uid).with_context(active_test=False).search([]).mapped('name')
