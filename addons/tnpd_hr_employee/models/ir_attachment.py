# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _check_contents(self, values):
        # In auth='none' routes env.uid is None.  The base implementation calls
        # self.env['ir.ui.view'].sudo(False).has_access('write') whose access
        # check in turn calls env.user._get_group_ids(), which fails with
        # "Expected singleton: res.users()" when uid is None.
        # Bypass by setting attachments_mime_plainxml in context so the
        # parent's short-circuit branch is taken (force_text=True for XML-like
        # content, which is safe and correct for SVG avatars generated during
        # user creation in a public context).
        if self.env.uid is None and not self.env.context.get('attachments_mime_plainxml'):
            return super(
                IrAttachment,
                self.with_context(attachments_mime_plainxml=True),
            )._check_contents(values)
        return super()._check_contents(values)
