# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, models

# Changing any of these on an institution can move the distance of an officer
# whose posting is recorded as a name in the old text fields.
_WATCHED = ('name', 'jail_type', 'active', 'latitude', 'longitude', 'geo_source', 'parent_id')


class PrisonJail(models.Model):
    """Keep officers posted by name (old text fields) in step with the institutions."""

    _inherit = 'prison.jail'

    @api.model
    def _refresh_officer_distances(self):
        employees = self.env['hr.employee'].sudo().with_context(active_test=False).search([
            '|', '|',
            ('x_central_prison', '!=', False),
            ('x_district_jail', '!=', False),
            ('x_sub_jail', '!=', False),
        ])
        employees._compute_x_distance()

    def _refresh_after_change(self):
        if not self.env.context.get('skip_officer_distance'):
            self._refresh_officer_distances()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._refresh_after_change()
        return records

    def write(self, vals):
        watched = [f for f in _WATCHED if f in vals]
        before = {rec.id: [rec[f] for f in watched] for rec in self}
        result = super().write(vals)
        # Only when a value really changed, so re-loading the same data on a
        # module upgrade does not touch every officer.
        if any(before[rec.id] != [rec[f] for f in watched] for rec in self):
            self._refresh_after_change()
        return result

    def unlink(self):
        result = super().unlink()
        self._refresh_after_change()
        return result
