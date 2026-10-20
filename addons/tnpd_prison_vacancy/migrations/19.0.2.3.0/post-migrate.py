from odoo import SUPERUSER_ID, api

from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates


def migrate(cr, version):
    if not version:
        return
    # Geographical location: this module adds institutions (women's and closed
    # ones) that prison_jail_master does not own, so seed their positions too.
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_prison_coordinates(env)
