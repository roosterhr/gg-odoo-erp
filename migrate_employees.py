import psycopg2
import psycopg2.extras

DEV = {
    "host": "147.224.200.248",
    "port": 5432,
    "dbname": "tnpd-prison-db",
    "user": "odoo",
    "password": "odoo@postgres",
}
LOCAL = {
    "host": "tnpd-prison-db",
    "port": 5432,
    "dbname": "tnpd",
    "user": "odoo",
    "password": "odoo",
}

FETCH_SQL = """
SELECT
    e.name, e.birthday, e.x_gender, e.active,
    e.certificate, e.study_field, e.study_school,
    e.place_of_birth, e.private_phone, e.mobile_phone,
    e.work_phone, e.work_email, e.emergency_contact,
    e.emergency_phone, e.private_car_plate,
    e.x_designation, e.x_employee_code, e.x_cps_no, e.x_gpf_no,
    e.x_cug_mobile, e.x_taluk, e.x_town, e.x_native_district,
    e.x_panel_year_sl_no, e.x_religion, e.x_community, e.x_caste,
    e.x_mother_tongue, e.x_central_prison, e.x_sub_jail, e.x_district_jail,
    e.x_date_of_appointment, e.x_date_of_retirement, e.x_date_present_station,
    e.x_permanent_address, e.x_education_qualification,
    e.x_service_history, e.x_medals, e.x_rewards,
    r.name AS res_name, r.resource_type, r.tz, r.time_efficiency
FROM hr_employee e
JOIN resource_resource r ON r.id = e.resource_id
WHERE e.active = true
  AND e.x_employee_code IS NOT NULL AND e.x_employee_code != ''
ORDER BY e.id
LIMIT 500;
"""

dev_conn = psycopg2.connect(**DEV)
local_conn = psycopg2.connect(**LOCAL)
dev_cur = dev_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
local_cur = local_conn.cursor()

dev_cur.execute(FETCH_SQL)
employees = dev_cur.fetchall()
print(f"Fetched {len(employees)} employees from dev.")

inserted = 0
skipped = 0

for emp in employees:
    code = emp["x_employee_code"]

    # Skip duplicates
    local_cur.execute("SELECT id FROM hr_employee WHERE x_employee_code = %s", (code,))
    if local_cur.fetchone():
        print(f"  SKIP (exists): {code} - {emp['name']}")
        skipped += 1
        continue

    # Insert resource_resource
    local_cur.execute("""
        INSERT INTO resource_resource
            (name, resource_type, tz, time_efficiency, active, company_id,
             create_uid, write_uid, create_date, write_date)
        VALUES (%s, %s, %s, %s, true, 1, 2, 2, NOW(), NOW())
        RETURNING id
    """, (emp["res_name"], emp["resource_type"], emp["tz"], emp["time_efficiency"]))
    res_id = local_cur.fetchone()[0]

    # Insert hr_employee
    local_cur.execute("""
        INSERT INTO hr_employee (
            resource_id, company_id, name, birthday, x_gender, active,
            certificate, study_field, study_school, place_of_birth,
            private_phone, mobile_phone, work_phone, work_email,
            emergency_contact, emergency_phone, private_car_plate,
            x_designation, x_employee_code, x_cps_no, x_gpf_no,
            x_cug_mobile, x_taluk, x_town, x_native_district,
            x_panel_year_sl_no, x_religion, x_community, x_caste,
            x_mother_tongue, x_central_prison, x_sub_jail, x_district_jail,
            x_date_of_appointment, x_date_of_retirement, x_date_present_station,
            x_permanent_address, x_education_qualification,
            x_service_history, x_medals, x_rewards,
            create_uid, write_uid, create_date, write_date
        ) VALUES (
            %s, 1, %s, %s, %s, true,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            2, 2, NOW(), NOW()
        ) RETURNING id
    """, (
        res_id, emp["name"], emp["birthday"], emp["x_gender"],
        emp["certificate"], emp["study_field"], emp["study_school"], emp["place_of_birth"],
        emp["private_phone"], emp["mobile_phone"], emp["work_phone"], emp["work_email"],
        emp["emergency_contact"], emp["emergency_phone"], emp["private_car_plate"],
        emp["x_designation"], emp["x_employee_code"], emp["x_cps_no"], emp["x_gpf_no"],
        emp["x_cug_mobile"], emp["x_taluk"], emp["x_town"], emp["x_native_district"],
        emp["x_panel_year_sl_no"], emp["x_religion"], emp["x_community"], emp["x_caste"],
        emp["x_mother_tongue"], emp["x_central_prison"], emp["x_sub_jail"], emp["x_district_jail"],
        emp["x_date_of_appointment"], emp["x_date_of_retirement"], emp["x_date_present_station"],
        emp["x_permanent_address"], emp["x_education_qualification"],
        emp["x_service_history"], emp["x_medals"], emp["x_rewards"],
    ))
    emp_id = local_cur.fetchone()[0]
    local_conn.commit()
    print(f"  INSERTED: {code} - {emp['name']} (local_id={emp_id})")
    inserted += 1

print(f"\nDone. Inserted: {inserted}, Skipped: {skipped}")
dev_conn.close()
local_conn.close()
