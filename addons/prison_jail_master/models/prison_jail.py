# Part of TNPD Prison Management System.
# License: LGPL-3

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PrisonJail(models.Model):
    """
    Tamil Nadu prison hierarchy master (2-tier flat model).

    Hierarchy rules (enforced by ``_check_hierarchy_integrity``):
        Parent types  (central_jail, spw) — no parent_id allowed
        Child  types  (all others)        — parent_id required;
                                            parent must be central_jail or spw

    ``hierarchy_type`` separates General (men) from Women institutions.
    ``is_closed`` marks operationally closed sub-jails; they remain in the
    database for employee history but are excluded from transfer destinations.

    ``_parent_store = True`` enables efficient subtree queries via
    ``parent_path``.  ``central_jail_id`` (stored computed) is now a single
    hop: parent for child records, self for parent records.
    """

    _name = 'prison.jail'
    _description = 'Prison / Jail'
    _rec_name = 'name'
    _order = 'sequence, jail_type, name'
    _parent_name = 'parent_id'
    _parent_store = True

    JAIL_TYPE = [
        ('central_jail',   'Central Prison'),
        ('spw',            'Special Prison for Women'),
        ('district_jail',  'District Jail'),
        ('sub_jail',       'Sub-Jail'),
        ('women_sub_jail', 'Women Sub-Jail'),
        ('special_sub_jail', 'Special Sub-Jail'),
        ('open_air_jail',  'Open Air Jail'),
        ('farm_jail',      'Farm Jail'),
        ('transit_yard',   'Transit Yard'),
    ]

    PARENT_TYPES = ('central_jail', 'spw')
    CHILD_TYPES  = (
        'district_jail', 'sub_jail', 'women_sub_jail', 'special_sub_jail',
        'open_air_jail', 'farm_jail', 'transit_yard',
    )

    # ── Core identity ─────────────────────────────────────────────────────────

    name = fields.Char(
        string='Jail Name',
        required=True,
        index=True,
    )
    code = fields.Char(
        string='Jail Code',
        index=True,
        copy=False,
        help='Short unique identifier (e.g. CP-CHN, SPW-CBE, SJ-POL).',
    )
    jail_type = fields.Selection(
        selection=JAIL_TYPE,
        string='Institution Type',
        required=True,
        index=True,
    )
    hierarchy_type = fields.Selection(
        selection=[('general', 'General'), ('women', 'Women')],
        string='Hierarchy',
        required=True,
        default='general',
        index=True,
    )

    # ── Hierarchy ─────────────────────────────────────────────────────────────

    parent_id = fields.Many2one(
        comodel_name='prison.jail',
        string='Parent Institution',
        index=True,
        ondelete='restrict',
    )
    parent_path = fields.Char(index=True)   # maintained by _parent_store
    child_ids = fields.One2many(
        comodel_name='prison.jail',
        inverse_name='parent_id',
        string='Child Institutions',
    )
    child_count = fields.Integer(
        string='Sub-units',
        compute='_compute_child_count',
    )
    # Flat 2-level: self if parent type, parent_id if child type.
    central_jail_id = fields.Many2one(
        comodel_name='prison.jail',
        string='Parent Prison',
        compute='_compute_central_jail_id',
        store=True,
        index=True,
    )

    # ── Closed status ─────────────────────────────────────────────────────────

    is_closed = fields.Boolean(
        string='Closed',
        default=False,
        index=True,
        help='Operationally closed. Staff may still be posted here but this '
             'institution cannot be selected as a transfer destination.',
    )
    closed_remarks = fields.Text(string='Closed Remarks')

    # ── Location ──────────────────────────────────────────────────────────────

    district = fields.Char(string='District', index=True)
    state_id = fields.Many2one(
        comodel_name='res.country.state',
        string='State',
        domain=[('country_id.code', '=', 'IN')],
    )
    latitude  = fields.Float(string='Latitude',  digits=(10, 6))
    longitude = fields.Float(string='Longitude', digits=(10, 6))

    # ── Hill Station ──────────────────────────────────────────────────────────

    is_hill_station = fields.Boolean(
        string='Hill Station',
        default=False,
        help='Officers posted here are eligible for transfer after 18 months.',
    )

    # ── Administration ────────────────────────────────────────────────────────

    superintendent_email = fields.Char(string='Superintendent Email')
    active   = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    external_ref = fields.Char(
        string='External Reference',
        index=True,
        copy=False,
        help='Reference ID used in legacy or external systems (e.g. PRIMS jail ID).',
    )
    notes = fields.Text(string='Notes')

    # ── SQL uniqueness constraints ────────────────────────────────────────────

    _uniq_code = models.Constraint(
        'UNIQUE (code)',
        'Jail code must be unique.',
    )
    _uniq_name_hierarchy = models.Constraint(
        'UNIQUE (name, hierarchy_type)',
        'An institution with this name already exists in the selected hierarchy.',
    )

    # ── Computed ──────────────────────────────────────────────────────────────

    def _compute_child_count(self):
        for rec in self:
            rec.child_count = self.env['prison.jail'].search_count(
                [('parent_id', '=', rec.id), ('active', '=', True)]
            )

    @api.depends('jail_type', 'parent_id')
    def _compute_central_jail_id(self):
        for rec in self:
            if rec.jail_type in self.PARENT_TYPES:
                rec.central_jail_id = rec
            else:
                rec.central_jail_id = rec.parent_id or False

    # ── Constraints ───────────────────────────────────────────────────────────

    @api.constrains('jail_type', 'parent_id', 'hierarchy_type')
    def _check_hierarchy_integrity(self):
        for rec in self:
            if rec.jail_type in self.PARENT_TYPES:
                if rec.parent_id:
                    raise ValidationError(
                        f'"{rec.name}" is a top-level institution and cannot '
                        'have a parent. Remove the parent before saving.'
                    )
                # SPW must be Women hierarchy; Central Prison must be General
                if rec.jail_type == 'spw' and rec.hierarchy_type != 'women':
                    raise ValidationError(
                        f'Special Prison for Women "{rec.name}" must have '
                        'Hierarchy = Women.'
                    )
                if rec.jail_type == 'central_jail' and rec.hierarchy_type != 'general':
                    raise ValidationError(
                        f'Central Prison "{rec.name}" must have Hierarchy = General.'
                    )
            else:
                # Child types — parent required and must be a top-level institution
                if not rec.parent_id:
                    raise ValidationError(
                        f'"{rec.name}" must be linked to a Central Prison or '
                        'Special Prison for Women via the Parent Institution field.'
                    )
                if rec.parent_id.jail_type not in self.PARENT_TYPES:
                    raise ValidationError(
                        f'The parent of "{rec.name}" must be a Central Prison or '
                        f'Special Prison for Women, but '
                        f'"{rec.parent_id.name}" is a '
                        f'{dict(self.JAIL_TYPE).get(rec.parent_id.jail_type, "unknown")}.'
                    )
                # Women child types must be under Women hierarchy
                if rec.jail_type == 'women_sub_jail' and rec.hierarchy_type != 'women':
                    raise ValidationError(
                        f'Women Sub-Jail "{rec.name}" must have Hierarchy = Women.'
                    )
                # General child types must be under General hierarchy
                if rec.jail_type in ('district_jail', 'sub_jail', 'open_air_jail',
                                     'farm_jail', 'transit_yard') \
                        and rec.hierarchy_type != 'general':
                    raise ValidationError(
                        f'"{rec.name}" ({dict(self.JAIL_TYPE).get(rec.jail_type)}) '
                        'must have Hierarchy = General.'
                    )

    # ── Onchange ──────────────────────────────────────────────────────────────

    @api.onchange('jail_type')
    def _onchange_jail_type(self):
        self.parent_id = False
        # Auto-set hierarchy_type based on institution type
        if self.jail_type in ('spw', 'women_sub_jail'):
            self.hierarchy_type = 'women'
        elif self.jail_type and self.jail_type != 'special_sub_jail':
            self.hierarchy_type = 'general'
        return {'domain': {'parent_id': self._parent_type_domain()}}

    @api.onchange('parent_id')
    def _onchange_parent_id(self):
        if not self.parent_id:
            return
        if not self.district:
            self.district = self.parent_id.district
        if not self.state_id:
            self.state_id = self.parent_id.state_id
        # Inherit hierarchy_type from parent
        if self.parent_id.hierarchy_type:
            self.hierarchy_type = self.parent_id.hierarchy_type

    def _parent_type_domain(self):
        """Restrict parent_id to top-level institutions only."""
        if self.jail_type in self.CHILD_TYPES:
            return [('jail_type', 'in', list(self.PARENT_TYPES)), ('active', '=', True)]
        return [('id', '=', False)]

    # ── Name search ───────────────────────────────────────────────────────────

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=100, order=None):
        domain = domain or []
        if name:
            domain = ['|', ('name', operator, name), ('code', operator, name)] + domain
        return self._search(domain, limit=limit, order=order)
