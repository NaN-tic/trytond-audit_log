# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from datetime import datetime, timedelta
from operator import itemgetter
from sql import Literal, Null, Cast, Union
from sql.operators import Concat
from sql.functions import DateTrunc

from trytond.model import ModelSQL, ModelView, fields, Unique
from trytond.wizard import Wizard, StateView, StateAction, Button
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.modules.jasper_reports.jasper import JasperReport

__all__ = ['AuditLog', 'OpenAuditLogStart', 'OpenAuditLogList', 'OpenAuditLog',
          'AuditLogReport', 'AuditLogType']


class AuditLogType(ModelSQL, ModelView):
    'Audit Log Type'
    __name__ = 'ir.audit.log.type'

    name = fields.Char('Name', required=True, translate=True)
    type_ = fields.Selection([
            ('create', 'Create'),
            ('write', 'Write'),
            ('delete', 'Delete'),
            ], 'Event Type', required=True)

    @classmethod
    def __setup__(cls):
        super(AuditLogType, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('type_uniq', Unique(t, t.type_),
                'The type of the Audit Log Type must be unique.'),
            ]


class AuditLog(ModelView):
    'Audit Log'
    __name__ = 'ir.audit.log'
    _order = [('date', 'DESC')]

    user = fields.Many2One('res.user', 'User', readonly=True)
    date = fields.DateTime('Date & Hour', readonly=True)
    type_ = fields.Many2One('ir.audit.log.type', 'Event Type', readonly=True)
    model = fields.Many2One('ir.model', 'Model')
    record = fields.Reference('Record', selection='models_get')
    history = fields.Boolean('History', readonly=True)
    changes = fields.Text('Changes', states={
            'invisible': ~Eval('history'),
            }, depends=['history'])

    @staticmethod
    def models_get():
        pool = Pool()
        Model = pool.get('ir.model')
        return [(m.model, m.name) for m in Model.search([])]

    @classmethod
    def get_common_columns(cls, table, model, history=False):
        reference_type = cls.model.sql_type().base
        columns = [
            Literal(history).as_('history'),
            Literal(model.id).as_('model'),
            Literal(model.rec_name).as_('model.rec_name'),
            Concat(model.model, Concat(',', Cast(table.id,
                        reference_type))).as_('record'),
            ]
        return columns

    @classmethod
    def get_create_columns(cls, table):
        pool = Pool()
        Type = pool.get('ir.audit.log.type')
        type_ = Type.search(['type_', '=', 'create'])
        type_ = type_ and type_[0] or None
        datetime = cls.date.sql_type().base
        return [
            Literal(type_.id).as_('type_'),
            Literal(type_.rec_name).as_('type_.rec_name'),
            table.create_uid.as_('user'),
            Cast(DateTrunc('seconds', table.create_date), datetime).as_(
                'date'),
            ]

    @classmethod
    def get_write_columns(cls, table):
        pool = Pool()
        Type = pool.get('ir.audit.log.type')
        type_ = Type.search(['type_', '=', 'write'])
        type_ = type_ and type_[0] or None
        datetime = cls.date.sql_type().base
        return [
            Literal(type_.id).as_('type_'),
            Literal(type_.rec_name).as_('type_.rec_name'),
            table.write_uid.as_('user'),
            Cast(DateTrunc('seconds', table.write_date), datetime).as_('date'),
            ]

    @classmethod
    def get_delete_columns(cls, table):
        pool = Pool()
        Type = pool.get('ir.audit.log.type')
        type_ = Type.search(['type_', '=', 'delete'])
        type_ = type_ and type_[0] or None
        datetime = cls.date.sql_type().base
        return [
            Literal(type_.id).as_('type_'),
            Literal(type_.rec_name).as_('type_.rec_name'),
            table.write_uid.as_('user'),
            Cast(DateTrunc('seconds', table.write_date), datetime).as_('date'),
            ]

    @classmethod
    def get_logs(cls, start):
        pool = Pool()
        Model = pool.get('ir.model')
        User = pool.get('res.user')
        cursor = Transaction().connection.cursor()

        types = []
        domain = []
        queries = []
        for t in start.types:
            types.append(t.type_)

        if start.models:
            domain.append(
                ('id', 'in', [m.id for m in start.models])
                )

        for model in Model.search(domain):
            if model.model == cls.__name__:
                continue
            try:
                Class = pool.get(model.model)
            except KeyError:
                # On pool init the model may not be available
                continue
            if not issubclass(Class, ModelSQL) or Class.table_query():
                continue

            if Class._history:
                table = Class.__table_history__()
                columns = cls.get_common_columns(table, model, Class._history)
                if not types or 'create' in types:
                    where = table.write_date == Null
                    if start.start_date:
                        where &= table.create_date >= start.start_date
                    if start.end_date:
                        where &= table.create_date <= start.end_date
                    if start.users:
                        where &= table.create_uid.in_(
                            [u.id for u in start.users])
                    queries.append(table.select(*(columns +
                                cls.get_create_columns(table)),
                            where=where))

                if not types or 'write' in types:
                    where = table.write_date != Null
                    if start.start_date:
                        where &= table.write_date >= start.start_date
                    if start.end_date:
                        where &= table.write_date <= start.end_date
                    if start.users:
                        where &= table.write_uid.in_(
                            [u.id for u in start.users])
                    queries.append(table.select(*(columns +
                                cls.get_write_columns(table)),
                            where=where))

                if not types or 'delete' in types:
                    where = table.create_date == Null
                    if start.start_date:
                        where &= table.write_date >= start.start_date
                    if start.end_date:
                        where &= table.write_date <= start.end_date
                    if start.users:
                        where &= table.write_uid.in_(
                            [u.id for u in start.users])
                    queries.append(table.select(*(columns +
                                cls.get_delete_columns(table)),
                            where=where))
            elif not types or 'write' in types:
                table = Class.__table__()
                columns = cls.get_common_columns(table, model)
                where = table.write_date != Null
                if start.start_date:
                    where &= table.write_date >= start.start_date
                if start.end_date:
                    where &= table.write_date <= start.end_date
                if start.users:
                    where &= table.write_uid.in_([u.id for u in start.users])
                queries.append(table.select(*(columns +
                            cls.get_write_columns(table)),
                        where=where))
        if not queries:
            return []
        sql, values = Union(*queries)
        result = []
        keys = ['history', 'model', 'model.rec_name', 'record', 'type_',
            'type_.rec_name', 'user', 'date']
        cursor.execute(*Union(*queries))

        for res in cursor.fetchall():
            audit_log = dict(zip(keys, res))
            user = audit_log.get('user')
            if user:
                audit_log['user.rec_name']= User(user).rec_name
            record = (audit_log.get('record') and
                audit_log['record'].split(',') or [])
            if record and len(record) == 2:
                Model = pool.get(record[0])
                try:
                    record = Model(record[1]).rec_name
                except:
                    record = record[1]
                audit_log['record.rec_name']= record
            result.append(audit_log)

        cls.get_changes(result)

        res = []
        if start.changes:
            for audit_log in result:
                if start.changes in audit_log['changes']:
                    res.append(audit_log)
        return start.changes and res or result

    @staticmethod
    def get_changes(result):
        pool = Pool()
        Field = pool.get('ir.model.field')
        Type = pool.get('ir.audit.log.type')

        for audit_log in result:
            type_ = Type(audit_log['type_'])
            record = (audit_log.get('record') and
                audit_log['record'].split(',') or [])
            if (not audit_log['history'] or type_.type_ != 'write' or
                not record or len(record) != 2):
                audit_log['changes'] = ''
                continue

            Model = pool.get(record[0])
            record = Model(record[1])
            Class = pool.get(record.__name__)
            _datetime = audit_log['date'] - timedelta(microseconds=1)
            changes = []

            with Transaction().set_context(_datetime=_datetime):
                history_model = Class(record.id)

            for field in Field.search([('model.model', '=', record.__name__)]):
                if field.ttype == 'one2many' or field.ttype == 'many2many':
                    continue
                if field.name not in Class._fields:
                    continue
                try:
                    new_value = getattr(record, field.name)
                    old_value = getattr(history_model, field.name)
                except:
                    continue
                if old_value != new_value:
                    if field.ttype == 'many2one' or field.ttype == 'reference':
                        old_value = old_value and old_value.rec_name or ''
                        new_value = new_value and new_value.rec_name or ''
                    changes.append('%s: %s -> %s' % (
                            field.field_description, old_value, new_value))
            audit_log['changes'] = '\n'.join(changes)


class OpenAuditLogStart(ModelView):
    'Open Audit Log Start'
    __name__ = 'ir.audit.log.open.start'

    users = fields.Many2Many('res.user', None, None, 'Users')
    start_date = fields.DateTime('Start Date & Hour')
    end_date = fields.DateTime('End Date & Hour')
    types = fields.Many2Many('ir.audit.log.type', None, None, 'Event Types')
    models = fields.Many2Many('ir.model', None, None, 'Models')
    changes = fields.Text('Changes')

    @staticmethod
    def default_start_date():
        return datetime.now() - timedelta(days=1)

    @staticmethod
    def default_end_date():
        return datetime.now()


class OpenAuditLogList(ModelView):
    'Open Audit Log Tree'
    __name__ = 'ir.audit.log.open.list'

    audit_logs = fields.One2Many('ir.audit.log', None, 'Audit Log',
        readonly=True)
    output_format = fields.Selection([
            ('pdf', 'PDF'),
            ('xls', 'XLS'),
            ], 'Output Format', required=True)

    @classmethod
    def list(cls, start):
        pool = Pool()
        AuditLog = pool.get('ir.audit.log')

        with Transaction().set_user(0, set_context=True):
            audit_logs = AuditLog.get_logs(start)

        return {
            'audit_logs': sorted(audit_logs, key=itemgetter('date'),
                reverse=True),
            'output_format': 'pdf',
            }


class AuditLogReport(JasperReport):
    __name__ = 'ir.audit.log.report'

    @classmethod
    def execute(cls, ids, data):
        parameters = {}
        return super(AuditLogReport, cls).execute(ids, {
            'name': 'audit_log.audit_log_report',
            'model': 'ir.audit.log',
            'data_source': 'records',
            'records': data['records'],
            'parameters': parameters,
            'output_format': data['output_format'],
            })


class OpenAuditLog(Wizard):
    'Open Audit Log'
    __name__ = 'ir.audit.log.open'

    start = StateView('ir.audit.log.open.start',
        'audit_log.audit_log_open_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Open', 'open_', 'tryton-ok', True),
            ])
    open_ = StateView('ir.audit.log.open.list',
        'audit_log.audit_log_open_list_view_form', [
            Button('Change', 'start', 'tryton-go-previous'),
            Button('Print', 'print_', 'tryton-print'),
            Button('Close', 'end', 'tryton-close'),
            ])
    print_ = StateAction('audit_log.report_audit_log')

    def default_open_(self, fields):
        pool = Pool()
        AuditLogList = pool.get('ir.audit.log.open.list')
        return AuditLogList.list(self.start)

    def do_print_(self, action):
        pool = Pool()
        User = pool.get('res.user')
        Type = pool.get('ir.audit.log.type')
        Model = pool.get('ir.model')
        records = []
        language = User(Transaction().user).language
        lang = language.code if language else 'en_US'
        for audit_log in self.open_.audit_logs:
            records.append({
                    'user': User(audit_log.user).name,
                    'date': audit_log.date,
                    'type': Type(audit_log.type_).name,
                    'model': Model(audit_log.model).rec_name,
                    'record': (audit_log.record and audit_log.record.rec_name
                        or ''),
                    'changes': audit_log.changes,
                    'lang': lang,
                    })

        data = {
            'records': records,
            'output_format': self.open_.output_format,
            }
        return action, data

    def transition_print_(self):
        return 'end'
