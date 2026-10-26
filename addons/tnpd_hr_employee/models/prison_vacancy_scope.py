# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import models


class PrisonVacancy(models.Model):
    _name = 'prison.vacancy'
    _inherit = ['prison.vacancy', 'tnpd.scope.mixin']

    def _tnpd_scope_domain(self, jail_ids):
        return [('prison_id', 'in', jail_ids)]


class PrisonDesignationVacancy(models.Model):
    _name = 'prison.designation.vacancy'
    _inherit = ['prison.designation.vacancy', 'tnpd.scope.mixin']

    def _tnpd_scope_domain(self, jail_ids):
        return [('prison_id', 'in', jail_ids)]
