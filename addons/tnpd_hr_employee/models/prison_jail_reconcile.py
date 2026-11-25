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
    def _heal_tree(self):
        """
        Rebuild every institution's tree path from its parent, then the stored central-prison links.

        Odoo's tree-path update matches EVERY row when the record being moved has an empty path
        (it builds its filter from that path), so one institution with an empty path turns a
        simple re-parenting into a table-wide rewrite. Some institutions here have an empty path,
        so this runs before any institution is moved, and again at the end. Returns how many
        central-prison links were repaired.
        """
        self.env.flush_all()
        self._parent_store_compute()
        self.invalidate_model(['parent_path'])
        return self._heal_central_links()

    @api.model
    def _heal_central_links(self):
        """
        Make the stored central-prison link of every institution equal to its parent again.
        It is a stored computed field that only follows ``parent_id`` when that is written through
        the ORM; records reactivated or edited another way can be left pointing at an old parent.
        Returns how many were repaired.
        """
        stale = self.with_context(active_test=False).search(
            [('jail_type', 'not in', list(self.PARENT_TYPES))]).filtered(lambda j: j.central_jail_id != j.parent_id)
        stale._compute_central_jail_id()
        return len(stale)

    @api.model
    def _find_among(self, names, jail_types, hierarchies):
        """The one record for a place known under several names: the one with staff first, then active, then the oldest."""
        keys = {self._master_key(n) for n in names if n}
        found = self.with_context(active_test=False).search(
            [('jail_type', 'in', list(jail_types)), ('hierarchy_type', 'in', list(hierarchies))]
        ).filtered(lambda j: self._master_key(j.name) in keys)
        if not found:
            return found
        staff = self._staff_counts(found.ids)
        return found.sorted(lambda j: (-staff.get(j.id, 0), not j.active, j.id))[:1]

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

    # ── merging a record into another ─────────────────────────────────────────

    # (table, column) pairs that hold a prison and can simply be pointed elsewhere
    _LINKS = (
        ('hr_employee', 'x_central_jail_id'), ('hr_employee', 'x_district_jail_id'), ('hr_employee', 'x_sub_jail_id'),
        ('transfer_approval_request', 'current_central_prison'), ('transfer_approval_request', 'current_district_jail'),
        ('transfer_approval_request', 'current_sub_jail'),
        ('transfer_approval_request', 'preference_2_central_prison'), ('transfer_approval_request', 'preference_2_district_jail'),
        ('transfer_approval_request', 'preference_2_sub_jail'),
        ('transfer_approval_request', 'preference_3_central_prison'), ('transfer_approval_request', 'preference_3_district_jail'),
        ('transfer_approval_request', 'preference_3_sub_jail'),
        ('transfer_approval_request', 'requested_central_prison'), ('transfer_approval_request', 'requested_district_jail'),
        ('transfer_approval_request', 'requested_sub_jail'),
    )

    @api.model
    def _move_links(self, old, new):
        """
        Point everything that refers to ``old`` at ``new``: officers, transfer requests, the
        institutions under it, the logins' jurisdictions and its vacancy figures. A vacancy row is
        moved only if ``new`` has none of its own (they are unique per institution), so nothing is
        lost or doubled. Returns how many rows moved.
        """
        cr, moved = self.env.cr, 0
        for table, column in self._LINKS:
            cr.execute('UPDATE %s SET %s = %%s WHERE %s = %%s' % (table, column, column), (new.id, old.id))
            moved += cr.rowcount
        # through the ORM, so the tree path and the stored central-prison link follow the new parent;
        # the cache is cleared first: ``new`` may have been created a moment ago, and its tree path
        # is only read correctly from the database
        self.env.invalidate_all()
        children = self.with_context(active_test=False).search([('parent_id', '=', old.id), ('id', '!=', new.id)])
        children.write({'parent_id': new.id})
        moved += len(children)
        cr.execute("""INSERT INTO res_users_jurisdiction_jail_rel (jail_id, user_id)
                        SELECT %s, r.user_id FROM res_users_jurisdiction_jail_rel r WHERE r.jail_id = %s
                        ON CONFLICT DO NOTHING""", (new.id, old.id))
        cr.execute('DELETE FROM res_users_jurisdiction_jail_rel WHERE jail_id = %s', (old.id,))
        cr.execute("""UPDATE prison_vacancy SET prison_id = %s WHERE prison_id = %s
                          AND NOT EXISTS (SELECT 1 FROM prison_vacancy w WHERE w.prison_id = %s)""", (new.id, old.id, new.id))
        moved += cr.rowcount
        cr.execute("""UPDATE prison_designation_vacancy v SET prison_id = %s WHERE v.prison_id = %s
                          AND NOT EXISTS (SELECT 1 FROM prison_designation_vacancy w
                                           WHERE w.prison_id = %s AND w.role_id = v.role_id
                                             AND w.hierarchy_type = v.hierarchy_type)""", (new.id, old.id, new.id))
        moved += cr.rowcount
        self.env.invalidate_all()
        return moved

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
    def _merge_from_row(self, Jail, row, rec, i, note):
        """
        ``merge_from`` on a row lists older records of the same place ({name, type, hierarchy}):
        everything linked to them moves to this record and they are switched off. Officers posted
        there keep their posting, now under the one record the list keeps.
        """
        for item in row.get('merge_from') or []:
            old = Jail._find_master(item['name'], jail_types=[item['type']],
                                    hierarchy_type=item.get('hierarchy'), exact_first=True)
            if not old or old == rec:
                continue
            moved = self._move_links(old, rec)
            if not moved and not old.active:
                continue  # folded in on an earlier run: nothing to do
            old.write({'active': False})
            note(i, 'merge', old.name, '%s rows moved to %s (id %s) and switched off' % (moved, rec.name, rec.id))

    @api.model
    def _apply_rows(self, rows, deactivate_missing):
        Jail = self.with_context(active_test=False)
        self._heal_tree()  # before anything is moved: see _heal_tree
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
                # a Central Prison and a Special Prison for Women can share a place name
                # (Coimbatore): look in the row's own hierarchy first
                parent_types = ('central_jail', 'spw', 'district_jail')
                parent = (Jail._find_master(row['parent'], jail_types=parent_types, hierarchy_type=hierarchy)
                          or Jail._find_master(row['parent'], jail_types=parent_types))
                if not parent:
                    errors += 1
                    note(i, 'error', name, 'parent "%s" is not in the master' % row['parent'])
                    return
            vals = {'name': name, 'jail_type': jail_type, 'hierarchy_type': hierarchy,
                    'parent_id': parent.id or False, 'active': True, 'is_closed': status == 'closed',
                    'closed_remarks': (row.get('remarks') or '').strip() or False}
            if 'district' in row:  # the district a sub-jail is listed under (blank: none)
                vals['district'] = (row.get('district') or '').strip() or False
            # optional match_name / match_type / match_hierarchy: the record is kept under another
            # spelling (or type), so the row updates it instead of adding a second one
            if row.get('match_name'):
                # under the old spelling or, if the list was applied before, under the new one:
                # take the record officers are posted at, never add a second record for the place
                rec = Jail._find_among(
                    [row['match_name'], name], {row.get('match_type') or jail_type, jail_type},
                    {row.get('match_hierarchy') or hierarchy, hierarchy})
            else:
                rec = Jail._find_master(name, jail_types=[jail_type], hierarchy_type=hierarchy, exact_first=True)
            if not rec:
                created = Jail.create(vals)
                touched.add(created.id)
                self._merge_from_row(Jail, row, created, i, note)
                note(i, 'add', name, 'new %s%s' % (jail_type, ' under ' + parent.name if parent else ''))
                return
            touched.add(rec.id)
            self._merge_from_row(Jail, row, rec, i, note)
            changes, to_write = [], {}
            for field, label in (('name', 'renamed'), ('jail_type', 'type'), ('hierarchy_type', 'hierarchy'),
                                 ('parent_id', 'moved'), ('active', 'reactivated'), ('is_closed', 'status'),
                                 ('closed_remarks', 'remarks'), ('district', 'district')):
                if field not in vals:
                    continue
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
                    to_write[field] = new
            if to_write:
                rec.write(to_write)  # one write, so the type / hierarchy / parent rules see the final state
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

        healed = self._heal_tree()
        if healed:
            note(0, 'heal', 'central-prison links', '%s repaired to follow their parent' % healed)

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
        self._heal_tree()  # before anything is moved: see _heal_tree

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
