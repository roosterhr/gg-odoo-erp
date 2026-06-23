# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import fields, models


class PrisonRole(models.Model):
    """
    Centralized Role / Designation master for TNPD prison staff.

    All role references across Employee, Vacancy, Transfer, and Reports must
    point here.  No free-text designation entry is allowed.

    gender_type drives the Women/General transfer validation:
        men   → only assignable to General hierarchy prisons
        women → only assignable to Women hierarchy (SPW) prisons
        both  → non-warder/administrative staff, no hierarchy restriction
    """

    _name = 'prison.role'
    _description = 'Prison Staff Role'
    _rec_name = 'name'
    _order = 'sequence, name'

    GENDER_TYPE = [
        ('men',   'Men'),
        ('women', 'Women'),
        ('both',  'Both / Administrative'),
    ]

    name = fields.Char(string='Role Name', required=True, index=True)
    gender_type = fields.Selection(
        selection=GENDER_TYPE,
        string='Gender Category',
        required=True,
        default='both',
        index=True,
        help='Determines which hierarchy this role can be posted to.',
    )
    active   = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    notes    = fields.Text(string='Notes')

    _uniq_role_name = models.Constraint(
        'UNIQUE(name)',
        'A role with this name already exists.',
    )
