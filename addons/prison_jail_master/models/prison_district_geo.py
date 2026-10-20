# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import api, fields, models

from .. import geo


class PrisonDistrictGeo(models.Model):
    """
    Centre point of each Tamil Nadu district.

    An officer's native place is recorded as a free-text district name. This
    table turns that name into a map point, tolerating spelling variants
    ("Thiruvannamalai" / "Tiruvannamalai"), so the distance to the prison
    where they are posted can be worked out.
    """

    _name = 'prison.district.geo'
    _description = 'District centre point'
    _order = 'name'

    name = fields.Char(string='District', required=True, index=True)
    alt_names = fields.Char(
        string='Other spellings',
        help='Comma-separated alternative spellings and local names.',
    )
    latitude = fields.Float(string='Latitude', digits=(10, 6), required=True)
    longitude = fields.Float(string='Longitude', digits=(10, 6), required=True)
    active = fields.Boolean(default=True)

    _uniq_name = models.Constraint(
        'UNIQUE (name)',
        'A district with this name already exists.',
    )

    @api.model
    def _lookup_table(self):
        """``{place_key: (latitude, longitude)}`` for every district and spelling."""
        table = {}
        for rec in self.search([]):
            labels = [rec.name] + (rec.alt_names or '').split(',')
            for label in labels:
                key = geo.place_key(label)
                if key:
                    table[key] = (rec.latitude, rec.longitude)
        return table
