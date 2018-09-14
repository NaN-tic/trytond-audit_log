# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from . import ir


def register():
    Pool.register(
        ir.AuditLog,
        ir.AuditLogType,
        ir.OpenAuditLogStart,
        ir.OpenAuditLogList,
        module='audit_log', type_='model')
    Pool.register(
        ir.OpenAuditLog,
        module='audit_log', type_='wizard')
    Pool.register(
        ir.AuditLogReport,
        module='audit_log', type_='report')
