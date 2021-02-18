# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import pytz
from datetime import datetime, timedelta
from operator import itemgetter
from jinja2 import Template
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from sql import Literal, Null, Cast, Union
from sql.operators import Concat
from sql.functions import DateTrunc

from trytond.model import ModelSQL, ModelView, fields, Unique
from trytond.wizard import Wizard, StateView, StateAction, Button
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.cache import Cache
from trytond.transaction import Transaction
from trytond.i18n import gettext
from trytond.config import config
from trytond.modules.jasper_reports.jasper import JasperReport

try:
    import html2text
except ImportError:
    html2text = None

__all__ = ['AuditLogNotification', 'AuditLogNotificationField', 'AuditLogType',
        'AuditLog', 'OpenAuditLogStart', 'OpenAuditLogList', 'OpenAuditLog',
        'AuditLogReport']

audit_log_notification = config.getint('queue', 'audit_log_notification', default=None)


class AuditLogNotificationMixin:

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        AuditLogNotification = pool.get('ir.audit.log.notification')

        model = cls.__name__

        # exclude some models
        if model.startswith(AuditLogNotification._check_notification_exclude):
            return super().create(vlist)

        if not AuditLogNotification._model_fields_cache.get('audit_notification'):
            AuditLogNotification.set_model_fields_cache()

        # check if model is notification
        notification = AuditLogNotification._model_fields_cache.get(model)
        if not notification:
            return super().create(vlist)

        user_id = Transaction().user
        timezone = AuditLogNotification.get_timezone()
        now = datetime.timestamp(datetime.now(timezone))

        res = super().create(vlist)

        to_notificate = {}
        count = 0
        for v in vlist:
            count -= 1
            for k in list(v.keys()):
                key = (model, k)
                notification = AuditLogNotification._model_fields_cache.get(key)
                if not notification:
                    continue

                if notification not in to_notificate:
                    to_notificate[notification] = {}

                value = AuditLogNotification.get_value(model, k, v[k])
                if value is None or value == '':
                    continue
                _vals = {
                    'model': model,
                    'field': k,
                    'current_value': value,
                    }

                _id = '%s,%s' % (model, count)
                if _id in to_notificate[notification]:
                    to_notificate[notification][_id] += [_vals]
                else:
                    to_notificate[notification][_id] = [_vals]

        if to_notificate:
            for notification, data in to_notificate.items():
                with Transaction().set_context(
                        queue_name='audit_log_notification',
                        queue_scheduled_at=audit_log_notification):
                    AuditLogNotification.__queue__.send_mail(notification,
                            data, user_id, now)
        return res

    @classmethod
    def write(cls, *args, **kwargs):
        if not AuditLogNotification._model_fields_cache.get('audit_notification'):
            AuditLogNotification.set_model_fields_cache()

        model = cls.__name__

        # exclude some models
        if model.startswith(AuditLogNotification._check_notification_exclude):
            return super().write(*args, **kwargs)

        # check if model is notification
        notification = AuditLogNotification._model_fields_cache.get(model)
        if not notification:
            return super().write(*args, **kwargs)

        user_id = Transaction().user
        timezone = AuditLogNotification.get_timezone()
        now = datetime.timestamp(datetime.now(timezone))

        to_notificate = {}
        notification_values = {}
        actions = iter(args)
        for records, values in zip(actions, actions):
            for record in records:
                notification_values[record.id] = {}
                for k in list(values.keys()):
                    key = (model, k)
                    notification = AuditLogNotification._model_fields_cache.get(key)
                    if not notification:
                        continue

                    if notification not in to_notificate:
                        to_notificate[notification] = {}

                    old_value = AuditLogNotification.get_value(model, k, getattr(record, k))
                    current_value = AuditLogNotification.get_value(model, k, values[k])
                    # TODO check o2m and create
                    if old_value != current_value:
                        notification_values[record.id][k] = (old_value, current_value)

        res = super().write(*args, **kwargs)

        actions = iter(args)
        for records, values in zip(actions, actions):
            for record in records:
                for k in list(values.keys()):
                    key = (model, k)
                    notification = AuditLogNotification._model_fields_cache.get(key)
                    if notification and notification_values[record.id].get(k):
                        _record = str(record)
                        _vals = {
                            'model': model,
                            'field': k,
                            'old_value': notification_values[record.id][k][0],
                            'current_value': notification_values[record.id][k][1],
                            }
                        if _record in to_notificate[notification]:
                            to_notificate[notification][_record] += [_vals]
                        else:
                            to_notificate[notification][_record] = [_vals]

        for notification, data in to_notificate.items():
            with Transaction().set_context(
                    queue_name='audit_log_notification',
                    queue_scheduled_at=audit_log_notification):
                AuditLogNotification.__queue__.send_mail(notification,
                        data, user_id, now)
        return res


class AuditLogNotification(ModelSQL, ModelView):
    'Audit Log Notification'
    __name__ = 'ir.audit.log.notification'
    name = fields.Char('Name', required=True)
    to = fields.Char('To', required=True)
    server = fields.Many2One('smtp.server', 'Server', required=True,
        domain=[('state', '=', 'done')])
    model_fields = fields.Many2Many('ir.audit.log.notification-ir.model.field',
        'notification', 'model_field', 'Fields', required=True)
    _model_fields_cache = Cache('ir.audit.log.notification.model_fields')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._check_notification_exclude = ('ir.', 'res.', 'babi.')

    @classmethod
    def get_timezone(cls):
        try:
            Company = Pool().get('company.company')
        except:
            pass
        company_id = Transaction().context.get('company')
        if company_id:
            company = Company(company_id)
            if company.timezone:
                return pytz.timezone(company.timezone)
        return pytz.timezone('UTC')

    def get_value(model, field, value):
        pool = Pool()
        Model = pool.get(model)
        Translation = pool.get('ir.translation')
        lang = Transaction().context.get('language', 'en')

        def _get_rec_name(record):
            try:
                return str(record.rec_name)
            except:
                return str(record)

        if isinstance(getattr(Model, field), fields.Many2One):
            if value:
                if isinstance(value, int):
                    return _get_rec_name(Model(value))
                else:
                    return _get_rec_name(value)
        elif isinstance(getattr(Model, field), fields.One2Many):
            if isinstance(value, list):
                return str([v[1] for v in value if v[0] == 'create'])
            else:
                return ', '.join(_get_rec_name(v) for v in value)
        elif isinstance(getattr(Model, field), fields.Selection):
            for k, v in getattr(Model, field).selection:
                if k == value:
                    key = (Model.__name__, 'selection', lang, v)
                    trans = Translation.get_sources([key])
                    value = trans.get(key) or v
                    break
            return value
        elif isinstance(getattr(Model, field), fields.MultiValue):
            if value:
                if isinstance(value, int):
                    return _get_rec_name(Pool().get(getattr(Model, field).model_name)(value))
                else:
                    return _get_rec_name(value)
        return value

    @classmethod
    def set_model_fields_cache(cls):
        for notification in cls.search([]):
            for mfield in notification.model_fields:
                key = (mfield.model.model, mfield.name)
                AuditLogNotification._model_fields_cache.set(mfield.model.model, True)
                AuditLogNotification._model_fields_cache.set(key, notification.id)
        cls._model_fields_cache.set('audit_notification', True)

    @classmethod
    def create(cls, vlist):
        cls._model_fields_cache.clear()
        res = super().create(vlist)
        cls.set_model_fields_cache()
        return res

    @classmethod
    def write(cls, *args):
        cls._model_fields_cache.clear()
        res = super().write(*args)
        cls.set_model_fields_cache()
        return res

    @classmethod
    def delete(cls, notifications):
        cls._model_fields_cache.clear()
        res = super().delete(notifications)
        cls.set_model_fields_cache()
        return res

    @classmethod
    def send_mail(cls, notification, data, user_id, date_):
        pool = Pool()
        User = pool.get('res.user')
        Model = pool.get('ir.model')

        notification = cls(notification)
        user = User(user_id)
        lang = user.language and user.language.code or 'en'
        dformat = (user.language and user.language.date or '%m/%d/%Y') + ', %H:%M:%S'
        date_ = datetime.fromtimestamp(date_).strftime(dformat)

        models = set()
        records = dict()
        with Transaction().set_context(language=lang):
            labels = {}
            labels['old_value'] = gettext('audit_log.msg_old_value')
            labels['current_value'] = gettext('audit_log.msg_current_value')
            labels['since'] = gettext('audit_log.msg_since')
            labels['new'] = gettext('audit_log.msg_new')
            labels['by'] = gettext('audit_log.msg_by')

            for key, values in data.items():
                if key in records:
                    continue

                model, id = key.split(',')
                if int(id) > 0:
                    # get rec_name record
                    rec_name = pool.get(model)(int(id)).rec_name

                    try:
                        rec_name = '%s (ID: %s)' % (pool.get(model)(int(id)).rec_name, id)
                    except:
                        rec_name = str(key)
                    records[key] = rec_name

                # get fields labels
                if model in models:
                    continue
                for model in Model.search([('model', '=', model)]):
                    for field in model.fields:
                        labels[(model.model, field.name)] = field.field_description
                models.add(model)

        tmpl = '''
<div style="background: #EEEEEE; padding-left: 10px; padding-bottom: 10px">
{% for key, values in data.items() %}
    <h2>{{ records.get(key, labels.new) }}</h2>
    {% for value in values %}<p>
        <strong>{{ labels[(value['model'], value['field'])] }}</strong><br/>
        {% if value.old_value %}<span style="color: SlateBlue">{{ labels.old_value }}: {{ value.old_value }}</span><br/>{% endif %}
        <span style="color: green">{{ labels.current_value }}: {{ value.current_value }}</span>
    </p>{% endfor %}
{% endfor %}
<small>{{ labels.by }} <b>{{ user.rec_name }}</b> {{ labels.since }} {{ date }}</small>
'''

        template = Template(tmpl)
        content = template.render({
            'date': date_,
            'data': data,
            'user': user,
            'labels': labels,
            'records': records})

        msg = MIMEMultipart('alternative')
        msg['From'] = notification.server.smtp_email
        msg['To'] = notification.to
        msg['Subject'] = Header(
                gettext('audit_log.msg_email_subject',
                database=Transaction().database.name,
                notification=notification.rec_name), 'utf-8')
        if html2text:
            converter = html2text.HTML2Text()
            part = MIMEText(
                converter.handle(content), 'plain', _charset='utf-8')
            msg.attach(part)
        part = MIMEText(content, 'html', _charset='utf-8')
        msg.attach(part)
        notification.server.send_mail(msg['From'], msg['To'], msg.as_string())


class AuditLogNotificationField(ModelSQL):
    'Audit Log Notification - Field'
    __name__ = 'ir.audit.log.notification-ir.model.field'
    _table = 'ir_audit_log_notification_field'
    notification = fields.Many2One('ir.audit.log.notification', 'Notification',
        ondelete='CASCADE', required=True, select=True)
    model_field = fields.Many2One('ir.model.field', 'Field',
        ondelete='CASCADE', required=True, select=True)


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
            Button('Change', 'start', 'tryton-back'),
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
