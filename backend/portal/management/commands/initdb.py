from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Create an empty database directly from the current models'

    def handle(self, *args, **options):
        models = [model for model in apps.get_models() if model._meta.managed and not model._meta.proxy]
        tables = set(connection.introspection.table_names())
        expected = {model._meta.db_table for model in models}
        if tables & expected:
            if not expected <= tables:
                raise CommandError('数据库结构不完整，请使用当前版本的完整数据库。')
            with connection.cursor() as cursor:
                for model in models:
                    columns = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
                    if not {field.column for field in model._meta.local_fields} <= columns:
                        raise CommandError('数据库结构与当前模型不一致：' + model._meta.db_table)
            self.stdout.write('Database ready.')
            return
        with connection.schema_editor() as editor:
            for model in models:
                editor.create_model(model)
        self.stdout.write('Database initialized from current models.')
