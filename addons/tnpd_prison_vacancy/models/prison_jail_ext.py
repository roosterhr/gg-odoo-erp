# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import fields, models


class PrisonJailExt(models.Model):
    _inherit = 'prison.jail'

    designation_vacancy_ids = fields.One2many(
        comodel_name='prison.designation.vacancy',
        inverse_name='prison_id',
        string='Designation Vacancies',
    )
