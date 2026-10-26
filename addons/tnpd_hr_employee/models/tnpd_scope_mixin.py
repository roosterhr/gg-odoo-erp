# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from odoo import models
from odoo.fields import Domain
from odoo.orm.domains import DomainCondition

SCOPE_CTX = 'tnpd_scope_uid'


class TnpdScopeMixin(models.AbstractModel):
    """
    Limits every search on the model to the caller's jurisdiction.

    Active only when the context carries ``tnpd_scope_uid`` (set by
    ``scope.scoped``) and that user is Regional; Headquarters is never limited.
    The controllers read through ``sudo()``, so Odoo record rules do not apply;
    this is the one place that does. ``search``, ``search_count`` and
    ``read_group`` all end in ``_search``.
    """

    _name = 'tnpd.scope.mixin'
    _description = 'TNPD jurisdiction filter'

    def _tnpd_scope_domain(self, jail_ids):
        """Domain that keeps only records inside ``jail_ids``. Set per model."""
        raise NotImplementedError

    def _tnpd_scope_ids_of(self, uid):
        """The user's institution ids (None = no limit), worked out once per request."""
        cache = self.env.cr.cache.setdefault('tnpd_scope_ids', {})
        if uid not in cache:
            # drop the scope from the context, or looking the user up would be filtered too
            user = self.env['res.users'].sudo().with_context(**{SCOPE_CTX: False}).browse(uid)
            cache[uid] = user._tnpd_scope_jail_ids()
        return cache[uid]

    @staticmethod
    def _tnpd_is_id_lookup(domain):
        """True for ``[('id', 'in', ids)]``: Odoo loading records it already holds (a related
        officer, jail, ...), which must work whatever the area. Real searches never look like this."""
        cond = Domain(domain)
        return isinstance(cond, DomainCondition) and cond.field_expr == 'id' and cond.operator == 'in'

    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        uid = self.env.context.get(SCOPE_CTX)
        if uid and not self._tnpd_is_id_lookup(domain):
            jail_ids = self._tnpd_scope_ids_of(uid)
            if jail_ids is not None:
                domain = Domain(domain) & Domain(self._tnpd_scope_domain(jail_ids))
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)
