import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # x_invited_by_admin gates the Admin Login (see res_users.py / employee_api.py
    # check_admin_session). Backfill True for the bootstrap admin only, so the
    # tightened admin-login check does not lock out the existing admin account.
    # Every other pre-existing internal user defaults to False and must be
    # re-invited through the admin invite flow to regain admin-login access.
    cr.execute("""
        UPDATE res_users
        SET x_invited_by_admin = TRUE
        WHERE login = 'admin'
    """)
    _logger.info(
        '[tnpd_hr_employee] post-migrate 19.0.2.3.0 — backfilled '
        'x_invited_by_admin=True for the bootstrap admin user'
    )
