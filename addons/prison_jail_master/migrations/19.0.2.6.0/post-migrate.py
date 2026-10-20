from odoo import SUPERUSER_ID, api

from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates


def migrate(cr, version):
    if not version:
        return
    # Geographical location: give existing institutions their starting
    # coordinates (matched by XML ID, then by name, so rebuilt databases work).
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_prison_coordinates(env)
