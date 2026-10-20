# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, models

# Changing any of these on a district can move an officer's distance.
_WATCHED = ('name', 'alt_names', 'latitude', 'longitude', 'active')


class PrisonDistrictGeo(models.Model):
    """Keep every officer's stored distance in step with the district table."""

    _inherit = 'prison.district.geo'

    @api.model
    def _refresh_officer_distances(self):
        employees = self.env['hr.employee'].sudo().with_context(active_test=False).search(
            [('x_native_district', '!=', False)]
        )
        employees._compute_x_distance()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._refresh_officer_distances()
        return records

    def write(self, vals):
        watched = [f for f in _WATCHED if f in vals]
        before = {rec.id: [rec[f] for f in watched] for rec in self}
        result = super().write(vals)
        # Only recompute when a value really changed, so re-loading the same
        # data on a module upgrade does not touch every officer.
        if any(before[rec.id] != [rec[f] for f in watched] for rec in self):
            self._refresh_officer_distances()
        return result

    def unlink(self):
        result = super().unlink()
        self._refresh_officer_distances()
        return result
