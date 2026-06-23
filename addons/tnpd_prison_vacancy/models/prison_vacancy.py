# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PrisonVacancy(models.Model):
    """
    Staff vacancy master for each Tamil Nadu prison facility.

    One record per prison (enforced by UNIQUE on prison_id).
    Seed data is loaded automatically on module installation via XML.
    Vacancy figures can be updated in bulk or individually through REST APIs
    without requiring code deployment.
    """

    _name = 'prison.vacancy'
    _description = 'Prison Staff Vacancy'
    _rec_name = 'prison_name'
    _order = 'prison_type, prison_name'

    PRISON_TYPE = [
        ('central_prison',   'Central Prison'),
        ('spw',              'Special Prison for Women'),
        ('district_jail',    'District Jail'),
        ('sub_jail',         'Sub-Jail'),
        ('women_sub_jail',   'Women Sub-Jail'),
        ('special_sub_jail', 'Special Sub-Jail'),
        ('open_air_jail',    'Open Air Jail'),
        ('farm_jail',        'Farm Jail'),
        ('transit_yard',     'Transit Yard'),
    ]

    # ── Identity ──────────────────────────────────────────────────────────────

    prison_id = fields.Many2one(
        comodel_name='prison.jail',
        string='Prison',
        required=True,
        ondelete='restrict',
        index=True,
    )
    prison_code = fields.Char(
        string='Prison Code',
        related='prison_id.code',
        store=True,
        readonly=True,
    )
    prison_name = fields.Char(
        string='Prison Name',
        required=True,
        index=True,
    )
    prison_type = fields.Selection(
        selection=PRISON_TYPE,
        string='Prison Type',
        required=True,
        index=True,
    )

    # ── Strength figures ──────────────────────────────────────────────────────

    sanctioned_strength = fields.Integer(
        string='Sanctioned Strength',
        default=0,
        help='Total number of approved staff positions.',
    )
    occupied_count = fields.Integer(
        string='Occupied Count',
        default=0,
        help='Number of positions currently filled.',
    )
    vacancy_count = fields.Integer(
        string='Vacancy Count',
        default=0,
        help='Number of vacant positions (Sanctioned – Occupied).',
    )

    # ── Status ────────────────────────────────────────────────────────────────

    active = fields.Boolean(default=True)

    # ── Designation vacancy (per-role breakdown) ──────────────────────────────

    designation_vacancy_ids = fields.One2many(
        comodel_name='prison.designation.vacancy',
        inverse_name='prison_id',
        string='Designation Vacancies',
        readonly=True,
    )

    # ── SQL uniqueness constraint ─────────────────────────────────────────────

    _prison_id_uniq = models.Constraint(
        'UNIQUE(prison_id)',
        'A vacancy record already exists for this prison.',
    )

    # ── Onchange ──────────────────────────────────────────────────────────────

    @api.onchange('prison_id')
    def _onchange_prison_id(self):
        if self.prison_id and not self.prison_name:
            self.prison_name = self.prison_id.name

    # ── Validation ────────────────────────────────────────────────────────────

    @api.constrains('sanctioned_strength', 'occupied_count', 'vacancy_count')
    def _check_counts(self):
        for rec in self:
            if rec.sanctioned_strength < 0:
                raise ValidationError('Sanctioned Strength cannot be negative.')
            if rec.occupied_count < 0:
                raise ValidationError('Occupied Count cannot be negative.')
            if rec.vacancy_count < 0:
                raise ValidationError('Vacancy Count cannot be negative.')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_vacancy_available(self):
        self.ensure_one()
        return self.vacancy_count > 0

    def recompute_from_designations(self):
        """Recompute prison-level totals from designation vacancy records."""
        self.ensure_one()
        desig = self.env['prison.designation.vacancy'].search(
            [('prison_id', '=', self.prison_id.id)]
        )
        self.write({
            'sanctioned_strength': sum(desig.mapped('sanctioned_strength')),
            'occupied_count':      sum(desig.mapped('filled_strength')),
            'vacancy_count':       sum(desig.mapped('vacancy_count')),
        })

    def as_api_dict(self):
        self.ensure_one()
        return {
            'prison_id': self.prison_id.id,
            'prison_name': self.prison_name,
            'prison_type': self.prison_type,
            'prison_code': self.prison_code or '',
            'sanctioned_strength': self.sanctioned_strength,
            'occupied_count': self.occupied_count,
            'vacancy_count': self.vacancy_count,
            'vacancy_available': self.is_vacancy_available(),
        }
