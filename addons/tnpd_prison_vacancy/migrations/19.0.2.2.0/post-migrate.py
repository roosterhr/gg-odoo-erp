# Part of TNPD Prison Management System.
# License: LGPL-3
#
# Mirrors Grade I/II warder data from old S.J. prison records to the new
# simplified prison records (ids 295-426) used by the prison breakdown UI.
# Rows are inserted with hierarchy_type='general_mirror' so they appear in
# the breakdown panel but are excluded from dashboard totals.
#
# Also:
#  - Activates 4 prisons incorrectly marked inactive
#  - Corrects hierarchy_type for new women-prison designation rows that were
#    accidentally set to 'women' (causing dashboard double-counting)

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info('[tnpd_prison_vacancy] post-migrate 19.0.2.2.0 — mirror Grade I/II data to simplified prison records')

    # ── 1. Activate prisons that were incorrectly marked inactive ──────────────
    cr.execute("""
        UPDATE prison_jail SET active = true
         WHERE id IN (311, 312, 335, 377) AND active = false
    """)
    _logger.info('[tnpd_prison_vacancy] reactivated %d prison records', cr.rowcount)

    # ── 2. Fix women-prison designation rows that should be general_mirror ─────
    # New women prisons (305,306,335, etc.) had hierarchy_type='women' rows
    # copied from the old women SJ records.  Those counted alongside the real
    # 'women' rows and inflated the dashboard totals.  Reclassify them so they
    # are visible in the breakdown but excluded from dashboard aggregates.
    cr.execute("""
        UPDATE prison_designation_vacancy
           SET hierarchy_type = 'general_mirror'
         WHERE prison_id IN (305,306,335,336,337,338,339,340,416,417)
           AND role_id IN (5,6)
           AND hierarchy_type = 'women'
    """)
    _logger.info('[tnpd_prison_vacancy] reclassified %d women-prison rows to general_mirror', cr.rowcount)

    # ── 3. Mirror Grade I/II from old records to new simplified records ────────
    # Each INSERT copies role_id 5 (Grade I Warder) and 6 (Grade II Warder)
    # from the source old-S.J. record to the target new-simplified record.
    # ON CONFLICT ensures re-runs are safe (idempotent).

    mirrors = [
        # (target_new_id, source_old_id)
        (308, 1),   # Chennai SJ ← CP Chennai I
        (307, 2),   # Chennai SJ (II) ← CP Chennai II
        (304, 4),   # Coimbatore SJ ← CP Coimbatore
        (303, 5),   # Coimbatore North ← CP Coimbatore North
        # 300 = Madurai SPW women — source must be old SPW Madurai (167), NOT CP Madurai (7)
        (300, 167), # Madurai SPW ← SPW Madurai women (old 167)
        (302, 8),
        (313, 12),
        # 416 = Villupuram Women SJ — source must be old Women SJ Villupuram (170), NOT DJ (20)
        (416, 170), # Villupuram Women SJ ← Women SJ Villupuram (old 170)
        (312, 29),
        (315, 34),
        (311, 35),
        (309, 42),
        (310, 43),
        (316, 44),
        (319, 45),
        (317, 50),
        (318, 51),
        (344, 60),
        (404, 61),
        (405, 62),
        (418, 69),
        (406, 73),
        (392, 78),
        (342, 89),
        (373, 99),
        (385, 101),
        (407, 103),
        (372, 113),
        (402, 118),
        (320, 122),
        (423, 139),
        (387, 144),
        (379, 145),
        (369, 161),
        (377, 168),
        (361, 180),
        (354, 184),
        (323, 185),
        (330, 186),
        (378, 188),
        (346, 189),
        (370, 190),
        (355, 191),
        (359, 192),
        (380, 193),
        (357, 195),
        (397, 196),
        (393, 197),
        (347, 198),
        # New CPs/SPWs 295-299 — were missing entirely
        (295, 163),  # Chennai SPW ← SPW Chennai (old 163)
        (296, 1),    # Chennai-I ← CP Chennai I (old 1)
        (297, 6),    # Coimbatore ← CP Coimbatore (old 6)
        (298, 165),  # Coimbatore SPW ← SPW Coimbatore (old 165)
        (299, 3),    # Cuddalore ← CP Cuddalore (old 3)
    ]

    inserted = 0
    for target_id, source_id in mirrors:
        cr.execute("""
            INSERT INTO prison_designation_vacancy
                   (prison_id, prison_name, role_id, role_name,
                    hierarchy_type, sanctioned_strength, filled_strength, vacancy_count)
            SELECT %s,
                   (SELECT name FROM prison_jail WHERE id = %s),
                   dv.role_id, dv.role_name,
                   'general_mirror',
                   dv.sanctioned_strength, dv.filled_strength, dv.vacancy_count
              FROM prison_designation_vacancy dv
             WHERE dv.prison_id = %s
               AND dv.role_id IN (5, 6)
               AND dv.hierarchy_type = 'general'
            ON CONFLICT (prison_id, role_id, hierarchy_type)
            DO UPDATE SET
                sanctioned_strength = EXCLUDED.sanctioned_strength,
                filled_strength     = EXCLUDED.filled_strength,
                vacancy_count       = EXCLUDED.vacancy_count,
                prison_name         = EXCLUDED.prison_name
        """, [target_id, target_id, source_id])
        inserted += cr.rowcount

    _logger.info('[tnpd_prison_vacancy] upserted %d general_mirror rows across %d prison mappings',
                 inserted, len(mirrors))
