from odoo import SUPERUSER_ID, api

from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates


def migrate(cr, version):
    if not version:
        return
    # Geographical location: give prisons their starting coordinates. The stored
    # distance fields on hr.employee are filled by Odoo when the module is
    # updated, and again here whenever a prison position is written.
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_prison_coordinates(env)
