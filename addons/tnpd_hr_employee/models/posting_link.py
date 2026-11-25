# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Linking officers to the institution records their typed posting names stand for.

Most officers were loaded with the prison written as text ("Central Prison, Chennai-2",
"CENTRAL PRISON - II CHENNAI", "S.J. Tirupattur") and are not linked to the institution record.
The institution panel, the vacancy figures, the jurisdiction filters and the dashboard follow the
links, so an officer who is only typed shows as missing from the prison.

``hr.employee._link_postings_to_institutions()`` reads the typed names, finds the one institution
each stands for and sets the missing links. It is careful rather than clever:

- it never changes a link that is already set;
- names are compared without the type words (Central Prison, Sub Jail, S.J., ...) and without
  regard to word order or to 1 / 01 / I and 2 / 02 / II ("II Chennai" is "Chennai - II");
- a near spelling is accepted only if one institution is clearly the best, and an institution
  under a different central prison than the officer's own is never chosen (Tirupathur and
  Tiruppattur are different places);
- a name that fits more than one institution ("Central Prison, Chennai" - I or II?) is left alone;
- a women's institution is not given to an officer whose designation says (Men);
- the three links must agree (a sub-jail under the officer's central prison).

It is safe to run again. It returns what it did and the typed names it could not place.
"""

import difflib
import logging
import re
from collections import Counter

from odoo import api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

_ALIASES = {
    'palayamcottai': 'palayamkottai', 'trichy': 'tiruchirappalli', 'vlr': 'vellore',
    'tiruttani': 'thiruthani', 'kanchipuram': 'kacheepuram', 'kokirakulam': 'kokkirakulam',
    'portonova': 'parangipet',
}
_NOISE = re.compile(
    r'\b(central\s*prison|special\s*prison\s*for\s*women|spw|district\s*jail|sub[\s-]*jail|ssj|oaj|osj|'
    r's\s*\.?\s*j|d\s*\.?\s*j|prison|jail|for\s*women|women|men|district|closed|annexe?)\b'
)
_BLANK = {'', 'nil', '-', '--', 'na', 'n/a', 'none'}
_CENTRAL_TYPES = ('central_jail', 'spw')
_SUB_TYPES = ('sub_jail', 'women_sub_jail', 'special_sub_jail', 'open_air_jail', 'farm_jail', 'transit_yard')
_FUZZY_MIN, _FUZZY_MARGIN = 0.88, 0.08
_FIELD_OF = {'central': 'x_central_jail_id', 'district': 'x_district_jail_id', 'sub': 'x_sub_jail_id'}


def posting_key(text):
    """A name reduced to what identifies the place: 'CENTRAL PRISON - II CHENNAI' is 'chennai ii'."""
    cleaned = _NOISE.sub(' ', (text or '').lower().replace('&', ' '))
    tokens = []
    for token in re.findall(r'[a-z0-9]+', cleaned):
        token = _ALIASES.get(token, token)
        if token in ('1', '01', 'i'):
            token = 'i'
        elif token in ('2', '02', 'ii'):
            token = 'ii'
        tokens.append(token)
    return ' '.join(sorted(tokens))


def _type_hint(text):
    """The kind of institution the typed name itself says, if it says one."""
    low = (text or '').lower()
    if 'special prison' in low or re.search(r'\bspw\b', low):
        return ('spw',)
    if 'central prison' in low:
        return ('central_jail',)
    if re.search(r'\bssj\b', low) or 'special sub' in low:
        return ('special_sub_jail',)
    if re.search(r'\b(oaj|osj)\b', low) or 'open air' in low:
        return ('open_air_jail',)
    if 'district jail' in low or re.search(r'\bd\s*\.?\s*j\b', low):
        return ('district_jail', 'central_jail')  # District Jail, Pudukkottai is a Central Prison now
    if 'women' in low and re.search(r'sub|\bs\s*\.?\s*j\b', low):
        return ('women_sub_jail',)
    return None


class HrEmployeePostingLink(models.Model):
    _inherit = 'hr.employee'

    @api.model
    def _heal_posting_levels(self):
        """
        Move a district link that points at a Central Prison or a Special Prison for Women into the
        central prison link. An institution that is folded into a central prison (the Delta district
        records into Pudukkottai) takes its officers' district link with it; a district link may only
        hold a District Jail. Returns how many officers were repaired.
        """
        Employee = self.sudo().with_context(active_test=False, tracking_disable=True, mail_notrack=True)
        wrong = Employee.search([('x_district_jail_id.jail_type', 'in', list(_CENTRAL_TYPES))])
        done = 0
        for officer in wrong:
            vals = {'x_district_jail_id': False}
            if not officer.x_central_jail_id:
                vals['x_central_jail_id'] = officer.x_district_jail_id.id
            if self._write_posting(officer, vals):
                done += 1
        return done

    @api.model
    def _write_posting(self, officer, vals):
        """
        Save the links of one officer. An officer whose existing links already contradict each other
        (a sub-jail that is not under their district jail) cannot be saved until that is put right;
        they are skipped and counted, not allowed to stop the rest.
        """
        try:
            with self.env.cr.savepoint():
                officer.write(vals)
            return True
        except ValidationError as exc:
            _logger.info('officer %s left as it is: %s', officer.x_employee_code, str(exc).splitlines()[0])
            return False

    @api.model
    def _posting_index(self):
        """The institutions to match against: [(record, key, root central id)]."""
        Jail = self.env['prison.jail'].sudo()
        index = []
        for jail in Jail.search([('active', '=', True)]):
            root = jail
            while root.parent_id and root.jail_type not in _CENTRAL_TYPES:
                root = root.parent_id
            index.append((jail, posting_key(jail.name), root.id if root.jail_type in _CENTRAL_TYPES else 0))
        return index

    @api.model
    def _resolve_posting(self, index, text, level, central_id=None, women_ok=True):
        """
        The one institution a typed name stands for, or None.

        ``level`` ('central', 'district' or 'sub') says which column the text came from: it picks
        the institution types to look at when the text does not say. ``central_id`` is the
        officer's central prison, when known: an institution under another one is not considered.
        """
        text = (text or '').strip()
        if text.lower() in _BLANK:
            return None
        key = posting_key(text)
        if not key:
            return None
        hint = _type_hint(text)
        default = {'central': _CENTRAL_TYPES, 'district': ('district_jail',), 'sub': _SUB_TYPES}[level]
        types = hint or default
        candidates = [(jail, jkey, root) for jail, jkey, root in index
                      if jail.jail_type in types and (women_ok or jail.hierarchy_type != 'women')]

        def in_context(root, jail):
            return not (central_id and root and root != central_id and jail.jail_type not in _CENTRAL_TYPES)

        exact = [c for c in candidates if c[1] == key]
        if exact:
            # a place name shared by several institutions is told apart by the officer's central prison
            pool = exact if len(exact) == 1 else [c for c in exact if in_context(c[2], c[0])]
            winner = pool[0] if len(pool) == 1 else None
        else:
            # judged against every institution first: the central prison breaks near ties, it does not
            # lower the bar (Tirupathur is not Tiruppattur just because it is the only one under it)
            scored = sorted(((difflib.SequenceMatcher(None, key, c[1]).ratio(), c) for c in candidates),
                            key=lambda item: -item[0])
            if not scored or scored[0][0] < _FUZZY_MIN:
                return None
            near = [c for ratio, c in scored if scored[0][0] - ratio < _FUZZY_MARGIN]
            pool = near if len(near) == 1 else [c for c in near if in_context(c[2], c[0])]
            winner = pool[0] if len(pool) == 1 else None
        if winner and in_context(winner[2], winner[0]):
            return winner[0]
        return None

    @api.model
    def _link_postings_to_institutions(self, dry_run=False, domain=None):
        """
        Set the missing institution links of officers from their typed posting names (see the module
        docstring). Returns {'officers': n, 'links': {'central': a, 'district': b, 'sub': c},
        'blocked_by_their_own_links': n, 'unplaced': [(typed name, officers), ...]}. With ``dry_run``
        nothing is written; ``domain`` limits
        the officers looked at.
        """
        if not dry_run:
            self._heal_posting_levels()
        index = self._posting_index()
        Employee = self.sudo().with_context(active_test=False, tracking_disable=True, mail_notrack=True)
        officers = Employee.search([
            ('active', '=', True), '|', '|',
            ('x_central_jail_id', '=', False), ('x_district_jail_id', '=', False), ('x_sub_jail_id', '=', False),
        ] + (domain or []))
        counts, unplaced, changed, blocked = Counter(), Counter(), 0, 0
        for officer in officers:
            women_ok = '(men)' not in (officer.x_designation or '').lower()
            current = {'central': officer.x_central_jail_id, 'district': officer.x_district_jail_id,
                       'sub': officer.x_sub_jail_id}
            columns = (('central', officer.x_central_prison), ('district', officer.x_district_jail),
                       ('sub', officer.x_sub_jail))
            found = {}
            # Each typed name can stand for an institution of any level (the central column sometimes
            # holds a district jail, the sub-jail column a women's prison): the result is placed by
            # the institution's own type. The central prison comes first: it guards the rest.
            for column, text in columns:
                text = (text or '').strip()
                central = current['central'] or found.get('central')
                jail = self._resolve_posting(index, text, column, central.id if central else None, women_ok)
                if jail:
                    slot = 'central' if jail.jail_type in _CENTRAL_TYPES else (
                        'district' if jail.jail_type == 'district_jail' else 'sub')
                    if not current[slot] and slot not in found:
                        found[slot] = jail
                elif text and text.lower() not in _BLANK and not text.lower().endswith('district') and not current[column]:
                    unplaced[(column, text)] += 1  # (a "... District" is a grouping, not an institution)
            central = current['central'] or found.get('central')
            district = current['district'] or found.get('district')
            vals = {}
            for slot, jail in found.items():
                if slot == 'district' and central and jail.parent_id != central:
                    continue
                if slot == 'sub':
                    if district and jail.parent_id != district:
                        continue
                    if not district and central and jail.parent_id and central not in (jail.parent_id, jail.parent_id.parent_id):
                        continue
                vals[_FIELD_OF[slot]] = jail.id
                counts[slot] += 1
            if vals:
                if dry_run or self._write_posting(officer, vals):
                    changed += 1
                else:
                    blocked += 1
                    for slot in found:
                        counts[slot] -= 1
        top = [('%s: %s' % key, n) for key, n in unplaced.most_common(15)]
        result = {'officers': changed, 'links': {k: v for k, v in counts.items() if v > 0},
                  'blocked_by_their_own_links': blocked, 'unplaced': top}
        _logger.info('officer postings linked to institutions%s: %s', ' (dry run)' if dry_run else '', result)
        return result
