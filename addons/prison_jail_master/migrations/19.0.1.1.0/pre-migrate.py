def migrate(cr, version):
    # Add geo / hill-station fields to prison_jail
    cr.execute("""
        ALTER TABLE prison_jail
            ADD COLUMN IF NOT EXISTS is_hill_station   BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS latitude          DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS longitude         DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS superintendent_email VARCHAR;
    """)

    # Flag the 4 hill-station sub jails (Ooty, Coonoor, Gudalur, Kodaikanal)
    cr.execute("""
        UPDATE prison_jail
        SET is_hill_station = TRUE
        WHERE id IN (106, 107, 108, 198)
          AND is_hill_station IS DISTINCT FROM TRUE;
    """)

    # Set coordinates for the 4 hill stations
    cr.execute("""
        UPDATE prison_jail SET latitude = 11.4102, longitude = 76.6950 WHERE id = 106;
        UPDATE prison_jail SET latitude = 11.3530, longitude = 76.7959 WHERE id = 107;
        UPDATE prison_jail SET latitude = 11.5013, longitude = 76.4988 WHERE id = 108;
        UPDATE prison_jail SET latitude = 10.2381, longitude = 77.4892 WHERE id = 198;
    """)
