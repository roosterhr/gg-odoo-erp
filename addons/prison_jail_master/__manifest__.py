# Part of TNPD Prison Management System.
# License: LGPL-3

{
    'name': 'Prison Jail Master',
    'version': '19.0.2.5.0',
    'category': 'Prison Management',
    'summary': 'Hierarchical master data for Tamil Nadu prison jails (v2 — flat 2-level)',
    'description': """
Prison Jail Master v2
=====================
Flat 2-level hierarchy: Central Prison / SPW  →  all child institutions

Supports two independent hierarchies:
    General (Men):  Central Prison  →  District Jail / Sub-Jail / Open Air / Farm / Transit
    Women:          SPW             →  Women Sub-Jail / Special Sub-Jail

Features
--------
* ``prison.jail`` model with hierarchy_type, is_closed, closed_remarks fields
* Stored computed ``central_jail_id`` for fast single-hop filtering
* Closed sub-jail management (excluded from transfer destinations)
* REST APIs: flat children endpoint, closed jails endpoint, backward-compat district/sub endpoints
* Migration script: scripts/migrate_hierarchy_v2.py
    """,
    'author': 'TNPD',
    'website': '',
    'license': 'LGPL-3',

    'depends': ['base', 'hr'],

    'data': [
        'security/ir.model.access.csv',
        'views/prison_jail_views.xml',
        'views/menu_items.xml',
        'data/prison_jail_data.xml',
    ],

    'installable': True,
    'application': True,
    'auto_install': False,
}
