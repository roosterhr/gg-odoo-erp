# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    # Geographical location: a roman numeral in a typed prison name ("Central
    # Prison - II Chennai") is now ignored when matching it to an institution,
    # so work every officer out again.
    env = api.Environment(cr, SUPERUSER_ID, {})
    employees = env['hr.employee'].with_context(active_test=False).search([])
    employees._compute_x_distance()
