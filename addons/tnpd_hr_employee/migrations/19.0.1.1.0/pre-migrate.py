from odoo.upgrade import util


def migrate(cr, version):
    # Drop the NOT NULL constraint on requested_sub_jail so central/district
    # jail employees can submit transfer requests without a sub jail selection.
    cr.execute("""
        ALTER TABLE transfer_approval_request
            ALTER COLUMN requested_sub_jail DROP NOT NULL;
    """)
