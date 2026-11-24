# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Requests have several reasons now: an old request's single reason becomes its one reason,
    # so it shows, filters and scores exactly as before.
    Request = env['transfer.approval.request'].with_context(active_test=False)
    reasons = {r.code: r for r in env['tnpd.transfer.reason'].with_context(active_test=False).search([])}
    moved = 0
    for rec in Request.search([('reason_category', '!=', False), ('reason_ids', '=', False)]):
        reason = reasons.get(rec.reason_category)
        if reason:
            rec.with_context(skip_reason_sync=True).write({'reason_ids': [(6, 0, reason.ids)]})
            moved += 1
    _logger.info('transfer requests: %s single reasons moved into the reasons list', moved)
    Request.search([])._compute_reason_score()  # the score is the sum of the weights of the reasons chosen

    # Institution master: the corrections the current data needs (Pudukkottai, Kodaikanal, Seyyur,
    # Active/Closed tags). Safe to run again.
    env['prison.jail']._apply_known_fixes()
