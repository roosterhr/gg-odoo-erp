# Part of TNPD Prison Management System.
# License: LGPL-3

from . import models
from . import controllers


def post_init_hook(env):
    """On a fresh install, give the institutions loaded so far their starting coordinates."""
    from .geo_seed import seed_prison_coordinates
    seed_prison_coordinates(env)
