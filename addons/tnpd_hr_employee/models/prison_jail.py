# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, models

from odoo.addons.prison_jail_master import geo

# Changing any of these on an institution can move the distance of an officer
# whose posting is recorded as a name in the old text fields.
_WATCHED = ('name', 'jail_type', 'active', 'latitude', 'longitude', 'geo_source', 'parent_id')

_TEXT_FIELDS = ('x_central_prison', 'x_district_jail', 'x_sub_jail')


class PrisonJail(models.Model):
    """Keep officers posted by name (old text fields) in step with the institutions."""

    _inherit = 'prison.jail'

    @api.model
    def _refresh_officer_distances(self, names=None):
        """
        Work out again the distance of officers whose posting is typed as a name.
        With ``names``, only those whose typed name stands for one of these
        institutions; otherwise all of them.
        """
        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)
        has_text = ['|', '|'] + [(field, '!=', False) for field in _TEXT_FIELDS]
        if names is None:
            Employee.search(has_text)._compute_x_distance()
            return
        wanted = {geo.place_core(name) for name in names} - {''}
        if not wanted:
            return
        cores = {}  # typed text -> its place, so each spelling is worked out once
        affected = []
        for row in Employee.search_read(has_text, list(_TEXT_FIELDS)):
            for field in _TEXT_FIELDS:
                text = row[field]
                if text and cores.setdefault(text, geo.place_core(text)) in wanted:
                    affected.append(row['id'])
                    break
        Employee.browse(affected)._compute_x_distance()

    def _names_with_descendants(self):
        """Names of these institutions and of every institution below them."""
        everything = self.sudo().with_context(active_test=False)
        return set(everything.search([('id', 'child_of', self.ids)]).mapped('name'))

    def _refresh_after_change(self, names):
        if names and not self.env.context.get('skip_officer_distance'):
            self._refresh_officer_distances(names)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get('skip_officer_distance'):
            records._refresh_after_change(records._names_with_descendants())
        return records

    def write(self, vals):
        watched = [f for f in _WATCHED if f in vals]
        if not watched or self.env.context.get('skip_officer_distance'):
            return super().write(vals)
        before = {rec.id: [rec[f] for f in watched] for rec in self}
        names = self._names_with_descendants()  # as they are named now
        result = super().write(vals)
        # Only when a value really changed, so re-loading the same data on a
        # module upgrade does not touch any officer.
        if any(before[rec.id] != [rec[f] for f in watched] for rec in self):
            self._refresh_after_change(names | self._names_with_descendants())
        return result

    def unlink(self):
        names = self._names_with_descendants()
        result = super().unlink()
        self.env['prison.jail']._refresh_after_change(names)
        return result
