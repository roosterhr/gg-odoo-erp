"""
TNPD Designation Vacancy CSV Import
====================================
Run inside Odoo shell:

    docker exec -i tnpd-prison-be bash -c \
      "cat /tmp/import_designation_vacancy.py | \
       odoo shell -d tnpd-prison-db --no-http -c /etc/odoo/odoo.conf"

Or copy this script into the container and run:
    exec(open('/tmp/import_designation_vacancy.py').read())

Source CSV columns: prison, type, parent, designation, sanctioned, filled, vacancy
"""

import csv
import io
import logging

_logger = logging.getLogger(__name__)

# Designation name normalization: CSV spelling → canonical Role Master name
# (handles the Jailor/Jailer variant and minor CSV differences)
DESIGNATION_MAP = {
    'Jailor':                       'Jailer',
    'Deputy Jailor':                'Deputy Jailer',
    'Assistant Jailor':             'Assistant Jailer',
    'Boot & Sandal Overseer':       'Boot and Sandal Overseer',
    'Supervisor - cum- Instructor': 'Supervisor-cum-Instructor',
}

# Prison name normalization: CSV spelling → canonical DB name
PRISON_NAME_OVERRIDES = {
    'Nagapattinam (UNCONFIRMED)': 'Nagapattinam',
    'Mannarkudi':                 'Mannargudi',
    'Periyakulam':                'Periakulam',
    'Purasadai Udaippu':          'Purasaraidaiudaippu',
    'Sirkazhi':                   'Sirkali',
    'Tharangampadi':              'Tharangambadi',
    'Thiruppathur':               'Tiruppathur',
    'Thiruvannamalai':            'Tiruvannamalai',
    'Thukalay':                   'Thuckalay',
    'Tiruttani':                  'Tiruthani',
    'Wallajah':                   'Walajah',
}


def _normalize_designation(raw):
    """Return canonical role name for a CSV designation string."""
    raw = raw.strip()
    return DESIGNATION_MAP.get(raw, raw)


def _find_prison(env, prison_name, prison_type, parent_name):
    """Find prison.jail record matching CSV row. Returns record or None."""
    Jail = env['prison.jail'].sudo()
    name = PRISON_NAME_OVERRIDES.get(prison_name.strip(), prison_name.strip())

    base_domain = [('active', '=', True)]
    type_domain = [('jail_type', '=', prison_type)] if prison_type else []

    # Exact match with type first (most specific)
    if prison_type:
        jail = Jail.search([('name', '=', name)] + type_domain + base_domain, limit=1)
        if jail:
            return jail

    # Exact match without type
    jail = Jail.search([('name', '=', name)] + base_domain, limit=1)
    if jail:
        return jail

    # Case-insensitive match with type
    if prison_type:
        jail = Jail.search([('name', 'ilike', name)] + type_domain + base_domain, limit=1)
        if jail:
            return jail

    # Case-insensitive match without type (last resort)
    jail = Jail.search([('name', 'ilike', name)] + base_domain, limit=1)
    if jail:
        return jail

    return None


def _get_or_create_role(env, raw_name):
    """Return existing prison.role, creating if not found."""
    canonical = _normalize_designation(raw_name)
    Role = env['prison.role'].sudo()
    role = Role.search([('name', '=', canonical)], limit=1)
    if not role:
        role = Role.create({'name': canonical, 'gender_type': 'both'})
        _logger.info('Created new role: %s', canonical)
    return role


def _upsert_designation_vacancy(env, prison, role, sanctioned, filled, vacancy_cnt):
    """Create or update prison.designation.vacancy record."""
    Desig = env['prison.designation.vacancy'].sudo()
    existing = Desig.search([
        ('prison_id', '=', prison.id),
        ('role_id', '=', role.id),
    ], limit=1)

    vals = {
        'sanctioned_strength': sanctioned,
        'filled_strength': filled,
    }
    if existing:
        existing.write(vals)
        return 'updated', existing
    else:
        vals.update({'prison_id': prison.id, 'role_id': role.id})
        rec = Desig.create(vals)
        return 'created', rec


def import_from_csv_string(env, csv_text):
    """
    Parse and import designation vacancy records from CSV text.

    Returns dict with summary counts.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    total = created = updated = failed = 0
    errors = []

    for row in reader:
        total += 1
        prison_name = row.get('prison', '').strip()
        designation  = row.get('designation', '').strip()

        if not prison_name or not designation:
            failed += 1
            errors.append(f'Row {total}: missing prison or designation — skipped.')
            continue

        try:
            sanctioned = int(row.get('sanctioned', 0) or 0)
            filled     = int(row.get('filled', 0) or 0)
            vacancy_c  = int(row.get('vacancy', 0) or 0)
        except ValueError:
            failed += 1
            errors.append(f'Row {total}: invalid numeric value for {prison_name}/{designation}.')
            continue

        # Resolve prison
        prison = _find_prison(
            env,
            prison_name,
            row.get('type', ''),
            row.get('parent', ''),
        )
        if not prison:
            failed += 1
            errors.append(f'Row {total}: prison "{prison_name}" not found in master — skipped.')
            continue

        # Resolve or create role
        role = _get_or_create_role(env, designation)

        # Upsert vacancy
        action, _ = _upsert_designation_vacancy(env, prison, role, sanctioned, filled, vacancy_c)
        if action == 'created':
            created += 1
        else:
            updated += 1

    # Recompute prison-level aggregate for each touched prison
    _recompute_prison_totals(env)

    env.cr.commit()

    summary = {
        'total': total,
        'created': created,
        'updated': updated,
        'failed': failed,
        'errors': errors[:50],   # cap returned errors to 50
    }
    print(
        f'IMPORT DONE  total={total}  created={created}  '
        f'updated={updated}  failed={failed}'
    )
    return summary


def _recompute_prison_totals(env):
    """Update prison.vacancy aggregate totals from designation vacancies."""
    Vacancy = env['prison.vacancy'].sudo()
    Desig = env['prison.designation.vacancy'].sudo()

    # Get all prisons that have designation vacancies
    prison_ids = Desig.search([]).mapped('prison_id.id')
    for pid in set(prison_ids):
        pv = Vacancy.search([('prison_id', '=', pid)], limit=1)
        if pv:
            pv.recompute_from_designations()


# ── When executed directly in Odoo shell ─────────────────────────────────────

if __name__ == '__main__' or 'env' in dir():
    CSV_PATH = '/tmp/role_vacancy_clean.csv'
    try:
        with open(CSV_PATH, 'r', encoding='utf-8') as f:
            csv_text = f.read()
        result = import_from_csv_string(env, csv_text)
        print(result)
    except FileNotFoundError:
        print(f'CSV file not found at {CSV_PATH}. '
              'Copy role_vacancy_clean.csv to /tmp/ inside the container first.')
