# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from . import models
from . import controllers


def post_init_hook(env):
    """On a fresh install, give prisons their starting coordinates."""
    from odoo.addons.prison_jail_master.geo_seed import seed_prison_coordinates
    seed_prison_coordinates(env)
