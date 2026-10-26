# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

from types import SimpleNamespace

from odoo import fields


def make_fixtures(env, tag):
    """
    Two central prisons with a sub-jail each, a user for the order screens and
    three officers of one designation (Grade I Warder) plus one of another.
    ``tag`` keeps names and codes unique between test classes.
    """
    Jail = env['prison.jail']
    fx = SimpleNamespace(tag=tag)
    fx.src_central = Jail.create({'name': '%s Source Central' % tag, 'jail_type': 'central_jail'})
    fx.src_sub = Jail.create({
        'name': '%s Source Sub' % tag, 'jail_type': 'sub_jail', 'parent_id': fx.src_central.id,
    })
    fx.dst_central = Jail.create({'name': '%s Target Central' % tag, 'jail_type': 'central_jail'})
    fx.dst_sub = Jail.create({
        'name': '%s Target Sub' % tag, 'jail_type': 'sub_jail', 'parent_id': fx.dst_central.id,
    })

    def officer(n, designation='Grade I Warder (Men)'):
        return env['hr.employee'].create({
            'name': '%s Officer %s' % (tag, n),
            'x_employee_code': '%s-%04d' % (tag.upper(), n),
            'x_designation': designation,
            'x_central_jail_id': fx.src_central.id,
            'x_sub_jail_id': fx.src_sub.id,
        })

    fx.officers = [officer(1), officer(2), officer(3)]
    fx.other_designation = officer(4, 'Jailor')
    return fx


def approved_transfer(env, fx, employee, transfer_type='request'):
    """An approved transfer of ``employee`` from the source to the target sub-jail."""
    request = env['transfer.approval.request'].create({
        'employee_id': employee.id,
        'transfer_type': transfer_type,
        'current_central_prison': fx.src_central.id,
        'current_sub_jail': fx.src_sub.id,
        'requested_central_prison': fx.dst_central.id,
        'requested_sub_jail': fx.dst_sub.id,
        'approval_user_id': env.user.id,
        'requested_by': env.user.id,
        'state': 'draft',
    })
    request.write({'state': 'approved', 'approved_date': fields.Datetime.now()})
    return request


def http_login(case, login, password=None):
    """
    Sign ``login`` in through the app's own sign-in URL, as the browser does.

    ``HttpCase.authenticate`` signs in without a web request and as non-interactive, which
    is exactly what a Regional login is refused (it must use the app's sign-in, not RPC).
    """
    import json
    reply = case.url_open(
        '/web/session/authenticate',
        data=json.dumps({'jsonrpc': '2.0', 'params': {
            'db': case.env.cr.dbname, 'login': login, 'password': password or login}}),
        headers={'Content-Type': 'application/json'},
    ).json()
    assert reply.get('result', {}).get('uid'), 'sign-in failed for %s: %s' % (login, reply)
