# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from .ir import *


def register():
    Pool.register(
        AuditLog,
        module='audit_log', type_='model')
