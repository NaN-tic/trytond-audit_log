# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from .ir import *


def register():
    Pool.register(
        AuditLog,
        AuditLogType,
        OpenAuditLogStart,
        OpenAuditLogList,
        module='audit_log', type_='model')
    Pool.register(
        OpenAuditLog,
        module='audit_log', type_='wizard')
    Pool.register(
        AuditLogReport,
        module='audit_log', type_='report')
