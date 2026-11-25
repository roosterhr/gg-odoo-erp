# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

import json
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Make the institution master equal to the client's approved Prison Master list (141
    institutions: 125 active, 16 closed, plus Kodaikanal temporarily closed).

    ``data/institution_master_rows.json`` is that list. Records the list does not keep are
    switched off, not deleted; officers, transfer requests, vacancy figures and logins linked to a
    record that is folded into another move to the one the list keeps (``merge_from``). The list is
    checked first: if any row does not fit, nothing is changed and the upgrade stops.
    """
    if not version:
        return  # a fresh install has no old data to bring in line
    env = api.Environment(cr, SUPERUSER_ID, {})
    Jail = env['prison.jail']
    with file_open('tnpd_hr_employee/data/institution_master_rows.json') as fh:
        rows = json.load(fh)

    check = Jail._reconcile_master(rows, apply=False, deactivate_missing=True)
    if check['summary'].get('errors'):
        bad = [a for a in check['actions'] if a['action'] == 'error']
        raise ValueError('Institution list does not fit the current data, nothing changed: %s' % bad[:5])

    result = Jail._reconcile_master(rows, apply=True, deactivate_missing=True)
    _logger.info('institution master set to the approved list: %s', result['summary'])
    _logger.info('institution master totals: %s', result['after'])
