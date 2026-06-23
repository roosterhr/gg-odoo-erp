# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Placeholder migration for tnpd_hr_employee 19.0.2.1.0.
# No DB schema changes needed — version bump ensures Odoo reloads the module
# so the updated transfer_approval_controller and employee_portal_api
# (designation gender-suffix fix) are active immediately after deploy.

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info('[tnpd_hr_employee] post-migrate 19.0.2.1.0 — no schema changes, reload only')
