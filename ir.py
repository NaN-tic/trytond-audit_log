# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from datetime import timedelta
from sql import Literal, Null, Cast, Union, Column
from sql.operators import Concat
from sql.functions import DateTrunc

from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction

__all__ = ['AuditLog']
__metaclass__ = PoolMeta


class AuditLog(ModelSQL, ModelView):
    'Audit Log'
    __name__ = 'ir.audit.log'
    _order = [('date', 'DESC')]

    user = fields.Many2One('res.user', 'User')
    date = fields.DateTime('Date & Hour')
    type = fields.Selection([
            ('create', 'Create'),
            ('write', 'Write'),
            ('delete', 'Delete'),
            ], 'Event Type')
    model = fields.Reference('Model', selection='models_get',
        select=True)
    history = fields.Boolean('History', readonly=True)
    changes = fields.Function(fields.Text('Changes',
            states={
                'invisible': ~Eval('history'),
                },
            depends=['history']),
        'get_changes')
    _changes_excluded_fields = set(['id', 'create_uid', 'write_uid',
            'create_date', 'write_date', 'rec_name'])

    @staticmethod
    def models_get():
        pool = Pool()
        Model = pool.get('ir.model')
        return [(m.model, m.name) for m in Model.search([])]

    @classmethod
    def get_common_columns(cls, table, model_name, lenght, i, history=False):
        bigint = fields.BigInteger('Test').sql_type().base
        reference_type = cls.model.sql_type().base
        id_ = Column(table, '__id') if history else table.id
        columns = [((Cast(id_, bigint) * lenght) + i).as_('id'),
            table.create_date.as_('create_date'),
            table.write_date.as_('write_date'),
            table.create_uid.as_('create_uid'),
            table.write_uid.as_('write_uid'),
            Literal(history).as_('history'),
            Concat(model_name, Concat(',', Cast(table.id,
                        reference_type))).as_('model'),
            ]
        return columns

    @classmethod
    def get_create_columns(cls, table):
        datetime = cls.date.sql_type().base
        return [Literal('create').as_('type'),
            table.create_uid.as_('user'),
            Cast(DateTrunc('seconds', table.create_date), datetime).as_(
                'date'),
            ]

    @classmethod
    def get_write_columns(cls, table):
        datetime = cls.date.sql_type().base
        return [Literal('write').as_('type'),
            table.write_uid.as_('user'),
            Cast(DateTrunc('seconds', table.write_date), datetime).as_('date'),
            ]

    @classmethod
    def get_delete_columns(cls, table):
        datetime = cls.date.sql_type().base
        return [Literal('delete').as_('type'),
            table.write_uid.as_('user'),
            Cast(DateTrunc('seconds', table.write_date), datetime).as_('date'),
            ]

    @classmethod
    def table_query(cls):
        pool = Pool()
        Model = pool.get('ir.model')
        queries = []
        models = Model.search([])
        queries_per_table = 3
        lenght = len(models) * queries_per_table
        for i, model in enumerate(models):
            i = i * queries_per_table
            if model.model == cls.__name__:
                continue
            try:
                Class = pool.get(model.model)
            except KeyError:
                # On pool init the model may not be available
                continue
            if not issubclass(Class, ModelSQL) or Class.table_query():
                continue
            table = Class.__table__()
            if Class._history:
                table = Class.__table_history__()
            columns = cls.get_common_columns(table, model.model, lenght, i,
                Class._history)
            queries.append(table.select(*(columns +
                        cls.get_create_columns(table)),
                    where=table.write_date == Null))
            if Class._history:
                table = Class.__table_history__()
                columns = cls.get_common_columns(table, model.model, lenght,
                    i + 1, True)
                queries.append(table.select(*(columns +
                            cls.get_write_columns(table)),
                        where=table.write_date != Null))
                table = Class.__table_history__()
                columns = cls.get_common_columns(table, model.model, lenght,
                    i + 2, True)
                queries.append(table.select(*(columns +
                            cls.get_delete_columns(table)),
                        where=table.create_date == Null))
            else:
                table = Class.__table__()
                columns = cls.get_common_columns(table, model.model, lenght,
                    i + 1)
                queries.append(table.select(*(columns +
                            cls.get_write_columns(table)),
                        where=table.write_date != Null))
        sql, params = Union(*queries)
        return Union(*queries)

    def get_changes(self, name):
        pool = Pool()
        Field = pool.get('ir.model.field')
        Class = pool.get(self.model.__name__)
        _datetime = self.date - timedelta(microseconds=1)
        changes = []
        if self.type != 'write':
            return ''
        with Transaction().set_context(_datetime=_datetime):
            history_model = Class(self.model.id)
        for field in Field.search([('model.model', '=', self.model.__name__)]):
            if field.name in self._changes_excluded_fields:
                continue
            if field.ttype == 'one2many' or field.ttype == 'many2many':
                continue
            if field.name not in Class._fields:
                continue
            new_value = getattr(self.model, field.name)
            old_value = getattr(history_model, field.name)
            if old_value != new_value:
                if field.ttype == 'many2one' or field.ttype == 'reference':
                    old_value = old_value and old_value.rec_name or ''
                    new_value = new_value and new_value.rec_name or ''
                changes.append('%s: %s -> %s' % (
                        field.field_description, old_value, new_value))
        return '\n'.join(changes)
