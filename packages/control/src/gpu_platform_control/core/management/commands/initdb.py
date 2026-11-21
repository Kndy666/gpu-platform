from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.sql import emit_post_migrate_signal
from django.db import connection
from django.db.models import DecimalField


class Command(BaseCommand):
    help = "Initialize the database from the current models without migration history"

    def handle(self, *args, **options):
        models = [model for model in apps.get_models() if model._meta.managed and not model._meta.proxy]
        all_models = [
            model
            for model in apps.get_models(include_auto_created=True)
            if model._meta.managed and not model._meta.proxy
        ]
        tables = set(connection.introspection.table_names())
        expected = {model._meta.db_table for model in all_models}
        if tables & expected:
            if not expected <= tables:
                raise CommandError(
                    "The database schema is incomplete. Restore or import a complete database."
                )
            with connection.cursor() as cursor:
                for model in all_models:
                    columns = {
                        column.name: column
                        for column in connection.introspection.get_table_description(
                            cursor, model._meta.db_table
                        )
                    }
                    for field in model._meta.local_fields:
                        column = columns.get(field.column)
                        if column is None:
                            raise CommandError("Database schema differs from the models: " + model._meta.db_table)
                        if (
                            isinstance(field, DecimalField)
                            and column.scale is not None
                            and column.scale < field.decimal_places
                        ):
                            raise CommandError(
                                f"Database numeric scale is too small for {model._meta.db_table}.{field.column}. "
                                f"Expected at least {field.decimal_places} decimal places."
                            )
        else:
            with connection.schema_editor() as editor:
                for model in models:
                    editor.create_model(model)
        emit_post_migrate_signal(options.get("verbosity", 1), False, connection.alias)
        self.stdout.write("Database ready.")
