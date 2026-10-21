from . import models
from . import controllers


def post_init_hook(env):
    """This module adds more institutions (women's and closed ones): give them positions too."""
    from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates
    seed_prison_coordinates(env)
