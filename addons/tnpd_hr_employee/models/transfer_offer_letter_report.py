# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import api, fields, models
from odoo.exceptions import UserError


def _dmy(value):
    return value.strftime('%d.%m.%Y') if value else ''


class ReportTransferOfferLetter(models.AbstractModel):
    """
    Values for the Transfer Offer Letter (proceedings) PDF of a transfer order.

    A draft prints with a DRAFT banner and no order number; a cancelled order is
    not printed. The institution an officer is moving *from* is read from the
    request, which saved it when it was raised, and not from the employee: once a
    transfer is approved the employee record already points at the new posting.
    """

    _name = 'report.tnpd_hr_employee.report_transfer_offer_letter'
    _description = 'Transfer Offer Letter report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['transfer.order'].browse(docids)
        cancelled = docs.filtered(lambda o: o.state == 'cancelled')
        if cancelled:
            raise UserError(
                'A cancelled transfer order cannot be printed (%s).'
                % ', '.join(cancelled.mapped('display_name'))
            )
        return {
            'doc_ids': docs.ids,
            'doc_model': docs._name,
            'docs': docs,
            'letter': {order.id: self._letter_values(order) for order in docs},
        }

    # ── helpers ───────────────────────────────────────────────────────────────

    @api.model
    def _institution(self, *jails):
        """Most specific of the given institutions as 'Type, Name', or ''."""
        for jail in jails:
            if jail:
                kind = dict(jail._fields['jail_type'].selection).get(jail.jail_type, '')
                return '%s, %s' % (kind, jail.name) if kind else jail.name
        return ''

    @api.model
    def _plural(self, designation):
        """'Grade I Warder (Men)' -> 'Grade I Warders (Men)'."""
        head, bracket, tail = designation.partition(' (')
        return '%ss%s%s' % (head, bracket, tail) if head else designation

    @api.model
    def _row(self, transfer):
        """Everything the letter says about one transfer."""
        employee = transfer.employee_id
        source_jail = transfer.current_sub_jail or transfer.current_district_jail or transfer.current_central_prison
        target_jail = transfer.requested_sub_jail or transfer.requested_district_jail or transfer.requested_central_prison
        source = self._institution(source_jail)
        target = self._institution(target_jail)
        approved = (
            fields.Datetime.context_timestamp(transfer, transfer.approved_date)
            if transfer.approved_date else None
        )
        return {
            'name': employee.name or '',
            'badge_no': employee.x_employee_code or '',
            'designation': employee.x_role_id.name or employee.x_designation or '',
            'source': source,
            'target': target,
            # The Superintendent in charge is the one of the central prison / SPW above.
            'source_super': self._institution(source_jail.central_jail_id) or source,
            'target_super': self._institution(target_jail.central_jail_id) or target,
            'ta_da': bool(transfer.ta_da_eligible),
            'admin_grounds': transfer.transfer_type == 'admin_grounds',
            'approved_on': _dmy(approved),
        }

    @api.model
    def _letter_values(self, order):
        rows = [self._row(t) for t in order.transfer_ids.sorted('id')]
        bulk = len(rows) > 1
        first = rows[0] if rows else {}
        order_date = order.order_date or fields.Date.context_today(order)
        designation = first.get('designation', '')

        if bulk:
            clauses = [
                'The following %s are transferred and posted to the places noted against each.'
                % self._plural(designation),
                'The Superintendents are requested to relieve the above individuals immediately.',
                'The dates of relief and joining of the individuals should be reported to this office.',
                'The receipt of this proceedings should be acknowledged forthwith.',
            ]
            heading = self._plural(designation)
        else:
            if first.get('admin_grounds'):
                order_text = (
                    '%s, %s (Employee ID No. %s), %s is transferred and posted to %s, '
                    'in an existing vacancy on administrative grounds.'
                    % (first['name'], designation, first['badge_no'], first['source'], first['target'])
                )
            else:
                order_text = (
                    'Tr. %s, %s (Employee ID No. %s), %s is transferred and posted to %s '
                    'in the existing vacancy.'
                    % (first['name'], designation, first['badge_no'], first['source'], first['target'])
                )
            clauses = [
                order_text,
                'The Superintendent, %s is instructed to relieve the above individual '
                'immediately.' % first['source_super'],
            ]
            if not first['ta_da']:
                clauses.append(
                    'The above individual is not eligible to claim any TA/DA since this '
                    "transfer is ordered on the individual's own request."
                )
            clauses += [
                'The dates of relief and joining of the individual should be reported '
                'to this office.',
                'The receipt of this proceedings should be acknowledged forthwith.',
            ]
            heading = designation

        supers = []
        for row in rows:
            for place in (row['source_super'], row['target_super']):
                if place and place not in supers:
                    supers.append(place)

        show_ref = bool(order.reference_letter or order.reference_date) or (
            not bulk and first.get('admin_grounds')
        )
        return {
            'is_draft': order.state == 'draft',
            'bulk': bulk,
            'number': order.name or '',
            'date': _dmy(order_date),
            'present': order.present or '',
            'dig_range': order.dig_range or '',
            'show_ref': show_ref,
            'ref_letter': order.reference_letter or '',
            'ref_date': _dmy(order.reference_date),
            'heading': heading,
            'clauses': clauses,
            'rows': [dict(row, no=i + 1) for i, row in enumerate(rows)],
            'addressed_to': supers,
            'source_super': first.get('source_super', ''),
            # footer of a single letter
            'badge_no': first.get('badge_no', ''),
            'name': first.get('name', ''),
            'approved_on': first.get('approved_on', ''),
            # kept for callers that read the single-letter fields directly
            'source': first.get('source', ''),
            'target': first.get('target', ''),
            'designation': designation,
        }
