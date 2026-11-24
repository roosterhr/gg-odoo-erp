# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Keeping the institution master equal to the client's list.

``_reconcile_master(rows)`` compares a list of institutions (the client's latest data) with
the records and adds, renames, moves, re-tags (Active / Closed) and, if asked, deactivates
so the two match. It can run as a dry run, which changes nothing and says what it would do,
so the plan and the final totals can be checked with the client before it is applied.

``_apply_known_fixes()`` holds the corrections the current data needs (Pudukkottai, Kodaikanal,
Seyyur). It is safe to run again: it only changes what is still wrong.
"""

import logging
import re

from odoo import api, models

_logger = logging.getLogger(__name__)

# words that only say what kind of institution it is, not which one
_NOISE = re.compile(
    r'\b(s\s*\.?\s*j|d\s*\.?\s*j|sub[\s-]*jail|district\s*jail|district|central\s*prison|'
    r'special\s*prison\s*for\s*women|spw|prison|closed|men|women)\b'
)


class _DryRun(Exception):
    """Raised to roll a dry run back; carries the plan."""

    def __init__(self, result):
        super().__init__('dry run')
        self.result = result


class PrisonJail(models.Model):
    _inherit = 'prison.jail'

    # ── names ─────────────────────────────────────────────────────────────────

    @api.model
    def _master_key(self, name):
        """A name without the kind-of-institution words: 'S.J. Keeranur (Closed)' is 'keeranur'."""
        text = _NOISE.sub(' ', (name or '').lower().replace('.', ' ').replace('(', ' ').replace(')', ' '))
        return re.sub(r'[^a-z0-9]+', ' ', text).strip()

    @api.model
    def _find_master(self, name, jail_types=None, hierarchy_type=None, exact_first=False):
        """The one record to use for a name: active first, then the one with staff, then the oldest."""
        key = self._master_key(name)
        if not key:
            return self.browse()
        domain = []
        if jail_types:
            domain.append(('jail_type', 'in', list(jail_types)))
        if hierarchy_type:
            domain.append(('hierarchy_type', '=', hierarchy_type))
        found = self.with_context(active_test=False).search(domain).filtered(
            lambda j: self._master_key(j.name) == key)
        if not found:
            return found
        staff = self._staff_counts(found.ids)
        exact = (name or '').strip().lower() if exact_first else None
        return found.sorted(lambda j: (exact is not None and (j.name or '').strip().lower() != exact,
                                       not j.active, -staff.get(j.id, 0), j.id))[:1]

    @api.model
    def _staff_counts(self, ids):
        """{jail id: officers posted there (by link or by the old typed name)}."""
        counts = {}
        if not ids:
            return counts
        self.env.cr.execute("""
            SELECT j.id, COUNT(DISTINCT e.id)
              FROM prison_jail j
              JOIN hr_employee e
                ON e.x_sub_jail_id = j.id OR e.x_district_jail_id = j.id OR e.x_central_jail_id = j.id
                OR lower(e.x_sub_jail) = lower(j.name) OR lower(e.x_district_jail) = lower(j.name)
                OR lower(e.x_central_prison) = lower(j.name)
             WHERE j.id = ANY(%s) AND e.active
             GROUP BY j.id
        """, (list(ids),))
        counts.update({row[0]: row[1] for row in self.env.cr.fetchall()})
        return counts

    # ── the client's list ─────────────────────────────────────────────────────

    @api.model
    def _master_totals(self):
        """The figures to confirm with the client: totals, and the split by type and status."""
        jails = self.with_context(active_test=False).search([])
        live = jails.filtered('active')
        by_type = {}
        for jail in live:
            by_type[jail.jail_type] = by_type.get(jail.jail_type, 0) + 1
        return {
            'active_total': len(live),
            'open': len(live.filtered(lambda j: not j.is_closed)),
            'closed': len(live.filtered('is_closed')),
            'inactive_records': len(jails) - len(live),
            'by_type': by_type,
            'women_or_special': len(live.filtered(
                lambda j: j.hierarchy_type == 'women' or j.jail_type in ('spw', 'special_sub_jail'))),
        }

    @api.model
    def _reconcile_master(self, rows, apply=False, deactivate_missing=False):
        """
        Make the master match ``rows``; ``apply=False`` only reports.

        Each row: ``name`` and ``jail_type`` are required; ``parent`` (a name), ``hierarchy_type``
        ('general' by default), ``status`` ('active' or 'closed') and ``remarks`` are optional.
        Returns {'applied', 'summary', 'actions', 'before', 'after'}.
        """
        before = self._master_totals()
        try:
            with self.env.cr.savepoint():
                result = self._apply_rows(rows, deactivate_missing)
                result['after'] = self._master_totals()
                if not apply:
                    raise _DryRun(result)
        except _DryRun as dry:
            result = dry.result
            self.env.invalidate_all()
        result['applied'] = bool(apply)
        result['before'] = before
        return result

    @api.model
    def _apply_rows(self, rows, deactivate_missing):
        Jail = self.with_context(active_test=False)
        valid_types = {key for key, _label in self._fields['jail_type'].selection}
        actions, touched, errors = [], set(), 0

        def note(i, action, name, detail=''):
            actions.append({'row': i, 'action': action, 'name': name, 'detail': detail})

        def process(i, row):
            nonlocal errors
            name = (row.get('name') or '').strip()
            jail_type = (row.get('jail_type') or '').strip()
            hierarchy = (row.get('hierarchy_type') or 'general').strip() or 'general'
            status = (row.get('status') or 'active').strip().lower()
            if not name or jail_type not in valid_types or status not in ('active', 'closed') \
                    or hierarchy not in ('general', 'women'):
                errors += 1
                note(i, 'error', name, 'needs a name, a valid type, status active or closed, hierarchy general or women')
                return
            parent = Jail.browse()
            if (row.get('parent') or '').strip():
                parent = Jail._find_master(row['parent'], jail_types=('central_jail', 'spw', 'district_jail'))
                if not parent:
                    errors += 1
                    note(i, 'error', name, 'parent "%s" is not in the master' % row['parent'])
                    return
            vals = {'name': name, 'jail_type': jail_type, 'hierarchy_type': hierarchy,
                    'parent_id': parent.id or False, 'active': True, 'is_closed': status == 'closed',
                    'closed_remarks': (row.get('remarks') or '').strip() or False}
            rec = Jail._find_master(name, jail_types=[jail_type], hierarchy_type=hierarchy, exact_first=True)
            if not rec:
                created = Jail.create(vals)
                touched.add(created.id)
                note(i, 'add', name, 'new %s%s' % (jail_type, ' under ' + parent.name if parent else ''))
                return
            touched.add(rec.id)
            changes = []
            for field, label in (('name', 'renamed'), ('jail_type', 'type'), ('parent_id', 'moved'),
                                 ('active', 'reactivated'), ('is_closed', 'status'),
                                 ('closed_remarks', 'remarks')):
                new = vals[field]
                old = rec[field].id if field == 'parent_id' else rec[field]
                if field == 'parent_id' and not row.get('parent'):
                    continue  # the list says nothing about the parent: leave it
                if field == 'closed_remarks' and not new:
                    continue
                if (old or False) != (new or False):
                    changes.append('%s: %s -> %s' % (
                        label, rec.parent_id.name if field == 'parent_id' else old,
                        parent.name if field == 'parent_id' else new))
                    rec.write({field: new})
            if changes:
                note(i, 'update', name, '; '.join(changes))
            else:
                note(i, 'unchanged', name)
            # other records of the same place are duplicates: switch them off
            twins = Jail.search([('id', '!=', rec.id), ('jail_type', '=', jail_type),
                                 ('hierarchy_type', '=', hierarchy), ('active', '=', True)]).filtered(
                lambda j: self._master_key(j.name) == self._master_key(name))
            for twin in twins:
                twin.write({'active': False})
                note(i, 'deactivate', twin.name, 'duplicate of %s (id %s)' % (rec.name, rec.id))

        for i, row in enumerate(rows, start=1):
            try:
                with self.env.cr.savepoint():
                    process(i, row)
            except Exception as exc:  # one bad row must not stop the others
                errors += 1
                note(i, 'error', (row.get('name') or '').strip(), str(exc).splitlines()[0][:200])

        if deactivate_missing:
            for jail in Jail.search([('active', '=', True), ('id', 'not in', list(touched))]):
                jail.write({'active': False})
                note(0, 'deactivate', jail.name, 'not in the client list')

        counts = {}
        for item in actions:
            counts[item['action']] = counts.get(item['action'], 0) + 1
        return {'summary': {'rows': len(rows), 'errors': errors, **counts}, 'actions': actions}

    # ── fixes the current data needs ──────────────────────────────────────────

    @api.model
    def _apply_known_fixes(self):
        """
        Corrections the institution data needs now. Safe to run again; returns what it changed.

        - every institution is tagged Active or Closed (no blank flag)
        - Pudukkottai: Aranthangi, Keeranur and Thirumayam sit under the Pudukkottai district jail,
          and there is one record each (the extra records are switched off)
        - Kodaikanal sits under the Madurai central prison, every record of it
        - Seyyur sub-jail exists (under Vellore, like the other Tiruvannamalai district sub-jails)
        """
        Jail = self.with_context(active_test=False)
        changed = []

        self.env.cr.execute("UPDATE prison_jail SET is_closed = FALSE WHERE is_closed IS NULL")
        if self.env.cr.rowcount:
            changed.append('tagged %s institutions with no Active/Closed flag as not closed' % self.env.cr.rowcount)
            Jail.invalidate_model(['is_closed'])

        district = Jail._find_master('Pudukkottai', jail_types=['district_jail'])
        if district:
            if not district.active:
                district.write({'active': True})
                changed.append('Pudukkottai district jail switched on')
            for name in ('Aranthangi', 'Keeranur', 'Thirumayam'):
                main = Jail._find_master(name, jail_types=['sub_jail'])
                if not main:
                    continue
                if main.parent_id != district:
                    old = main.parent_id.name or 'no parent'
                    main.write({'parent_id': district.id})
                    changed.append('%s moved from %s to %s' % (main.name, old, district.name))
                if not main.active:
                    main.write({'active': True})
                    changed.append('%s switched on' % main.name)
                for twin in Jail.search([('id', '!=', main.id), ('jail_type', '=', 'sub_jail'), ('active', '=', True)]):
                    if self._master_key(twin.name) == self._master_key(name):
                        twin.write({'active': False})
                        changed.append('duplicate %s (id %s) switched off' % (twin.name, twin.id))

        madurai = Jail._find_master('Madurai', jail_types=['central_jail'])
        if madurai:
            for kodai in Jail.search([('jail_type', '=', 'sub_jail')]).filtered(
                    lambda j: self._master_key(j.name) == 'kodaikanal'):
                if kodai.parent_id != madurai:
                    old = kodai.parent_id.name or 'no parent'
                    kodai.write({'parent_id': madurai.id})
                    changed.append('%s (id %s) moved from %s to %s' % (kodai.name, kodai.id, old, madurai.name))

        vellore = Jail._find_master('Vellore', jail_types=['central_jail'])
        if vellore and not Jail._find_master('Seyyur', jail_types=['sub_jail']):
            Jail.create({'name': 'Seyyur', 'jail_type': 'sub_jail', 'hierarchy_type': 'general',
                         'parent_id': vellore.id, 'is_closed': False, 'active': True})
            changed.append('Seyyur sub-jail added under %s' % vellore.name)

        changed += self._apply_client_list_page()

        for line in changed:
            _logger.info('institution master fix: %s', line)
        return changed

    # The client's list, districts 71 to 83 (Kanniyakumari to Thiruvarur): the district jails they
    # sit under, the sub-jails of each, and the temporarily closed Kodaikanal under Madurai.
    _CLIENT_DISTRICTS = ('Kanniyakumari', 'Pudukkottai', 'Thanjavur', 'Mayiladuthurai', 'Thiruvarur', 'Nagapattinam')
    _CLIENT_SUB_JAILS = (  # (district jail, sub-jail)
        ('Kanniyakumari', 'Kuzhithurai'), ('Pudukkottai', 'Thirumayam'), ('Pudukkottai', 'Aranthangi'),
        ('Thanjavur', 'Papanasam'), ('Thanjavur', 'Thiruvidaimaruthur'), ('Thanjavur', 'Kumbakonam'),
        ('Thanjavur', 'Thanjavur'), ('Mayiladuthurai', 'Sirkali'), ('Mayiladuthurai', 'Mayiladuthurai'),
        ('Mayiladuthurai', 'Tharangambadi'), ('Thiruvarur', 'Thiruthuraipoondi'), ('Thiruvarur', 'Nannilam'),
        ('Thiruvarur', 'Mannarkudi'),
    )

    @api.model
    def _find_near(self, name, parent):
        """
        A sub-jail of ``parent`` whose name is a near spelling of ``name`` ('Mannargudi' for
        'Mannarkudi'), else empty. Spelling differences are not new institutions.
        """
        import difflib
        key = self._master_key(name)
        best, best_ratio = self.browse(), 0.0
        for child in self.with_context(active_test=False).search(
                [('parent_id', '=', parent.id), ('jail_type', '=', 'sub_jail')]):
            ratio = difflib.SequenceMatcher(None, key, self._master_key(child.name)).ratio()
            if ratio > best_ratio:
                best, best_ratio = child, ratio
        return best if best_ratio >= 0.85 else self.browse()

    @api.model
    def _apply_client_list_page(self):
        """
        Make sure the district jails and sub-jails on that page of the client's list are in the
        master and switched on: a district jail that is switched off hides every sub-jail under it
        from the institution-wise view. Safe to run again.
        """
        Jail = self.with_context(active_test=False)
        changed = []
        districts = {}
        for name in self._CLIENT_DISTRICTS:
            district = Jail._find_master(name, jail_types=['district_jail'])
            if not district:
                continue
            districts[name] = district
            if not district.active:
                district.write({'active': True})
                changed.append('%s district jail switched on (it is on the client list)' % district.name)
            # one record per district jail: another active record of the same place is a duplicate
            for twin in Jail.search([('id', '!=', district.id), ('jail_type', '=', 'district_jail'), ('active', '=', True)]):
                if self._master_key(twin.name) == self._master_key(name):
                    twin.write({'active': False})
                    changed.append('duplicate district jail %s (id %s) switched off' % (twin.name, twin.id))

        for district_name, sub_name in self._CLIENT_SUB_JAILS:
            district = districts.get(district_name)
            if not district:
                continue
            sub = Jail._find_master(sub_name, jail_types=['sub_jail'])
            if not sub:
                near = Jail._find_near(sub_name, district)
                if near:
                    old = near.name
                    near.write({'name': sub_name})
                    changed.append('%s renamed to %s, as on the client list' % (old, sub_name))
                    continue
                Jail.create({'name': sub_name, 'jail_type': 'sub_jail', 'hierarchy_type': 'general',
                             'parent_id': district.id, 'is_closed': False, 'active': True})
                changed.append('%s sub-jail added under %s' % (sub_name, district.name))
            else:
                if sub.parent_id != district:
                    old = sub.parent_id.name or 'no parent'
                    sub.write({'parent_id': district.id})
                    changed.append('%s moved from %s to %s, as on the client list' % (sub.name, old, district.name))
                if not sub.active and not sub.is_closed:
                    sub.write({'active': True})
                    changed.append('%s sub-jail switched on' % sub.name)

        # Kodaikanal: temporarily closed, under Madurai
        madurai = Jail._find_master('Madurai', jail_types=['central_jail'])
        kodai = Jail._find_master('Kodaikanal', jail_types=['sub_jail'])
        if madurai and kodai:
            vals = {}
            if kodai.parent_id != madurai:
                vals['parent_id'] = madurai.id
            if not kodai.is_closed:
                vals['is_closed'] = True
            if not kodai.closed_remarks:
                vals['closed_remarks'] = 'Temporarily closed'
            if vals:
                kodai.write(vals)
                changed.append('Kodaikanal marked temporarily closed under Madurai')
        return changed
