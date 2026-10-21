# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    # Geographical location: officers whose posting is only a name in the old
    # text fields now get a distance too, so work every officer out again.
    env = api.Environment(cr, SUPERUSER_ID, {})
    employees = env['hr.employee'].with_context(active_test=False).search([])
    employees._compute_x_distance()
