# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PrisonDesignationVacancy(models.Model):
    """
    Per-role vacancy record for each prison.

    One record per (prison, role) pair — enforced by UNIQUE constraint.
    vacancy_count is always derived from sanctioned_strength − filled_strength.

    Used for:
        * Transfer approval vacancy gate (must have vacancy_count > 0)
        * Women/General role-hierarchy enforcement
        * Dashboard designation summary
        * CSV bulk import
    """

    _name = 'prison.designation.vacancy'
    _description = 'Prison Designation-wise Vacancy'
    _rec_name = 'display_name'
    _order = 'prison_id, role_id'

    # ── Identity ──────────────────────────────────────────────────────────────

    prison_id = fields.Many2one(
        comodel_name='prison.jail',
        string='Prison',
        required=True,
        ondelete='restrict',
        index=True,
    )
    prison_name = fields.Char(
        string='Prison Name',
        related='prison_id.name',
        store=True,
        readonly=True,
    )
    hierarchy_type = fields.Selection(
        related='prison_id.hierarchy_type',
        string='Hierarchy',
        store=True,
        readonly=True,
    )
    role_id = fields.Many2one(
        comodel_name='prison.role',
        string='Role / Designation',
        required=True,
        ondelete='restrict',
        index=True,
    )
    role_name = fields.Char(
        string='Role Name',
        related='role_id.name',
        store=True,
        readonly=True,
    )
    display_name = fields.Char(
        string='Display Name',
        compute='_compute_display_name',
        store=True,
    )

    # ── Vacancy figures ───────────────────────────────────────────────────────

    sanctioned_strength = fields.Integer(string='Sanctioned', default=0)
    filled_strength     = fields.Integer(string='Filled',     default=0)
    vacancy_count       = fields.Integer(
        string='Vacancy',
        compute='_compute_vacancy',
        store=True,
    )
    last_updated = fields.Datetime(
        string='Last Updated',
        default=fields.Datetime.now,
        help='Set automatically on create; update manually via import.',
    )

    # ── SQL uniqueness ────────────────────────────────────────────────────────

    _uniq_prison_role = models.Constraint(
        'UNIQUE(prison_id, role_id, hierarchy_type)',
        'A vacancy record already exists for this prison + role + hierarchy_type combination.',
    )

    # ── Computed ──────────────────────────────────────────────────────────────

    @api.depends('prison_name', 'role_name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f'{rec.prison_name or ""} — {rec.role_name or ""}'

    @api.depends('sanctioned_strength', 'filled_strength')
    def _compute_vacancy(self):
        for rec in self:
            rec.vacancy_count = max(0, rec.sanctioned_strength - rec.filled_strength)

    # ── Validation ────────────────────────────────────────────────────────────

    @api.constrains('sanctioned_strength', 'filled_strength')
    def _check_counts(self):
        for rec in self:
            if rec.sanctioned_strength < 0:
                raise ValidationError('Sanctioned Strength cannot be negative.')
            if rec.filled_strength < 0:
                raise ValidationError('Filled Strength cannot be negative.')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_vacancy_available(self):
        self.ensure_one()
        return self.vacancy_count > 0

    def as_api_dict(self):
        self.ensure_one()
        return {
            'prison_id':           self.prison_id.id,
            'prison_name':         self.prison_name,
            'hierarchy_type':      self.hierarchy_type or 'general',
            'role_id':             self.role_id.id,
            'role_name':           self.role_name,
            'sanctioned_strength': self.sanctioned_strength,
            'filled_strength':     self.filled_strength,
            'vacancy_count':       self.vacancy_count,
            'vacancy_available':   self.is_vacancy_available(),
        }
