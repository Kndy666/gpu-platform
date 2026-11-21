from pathlib import Path
import pytest
from gpu_platform_control.settings.database import database_settings


def test_sqlite_relative_database_uses_state_directory(tmp_path):
    result = database_settings({'engine': 'sqlite', 'name': 'records.db'}, tmp_path)
    assert Path(result['NAME']) == tmp_path / 'records.db'
    assert result['OPTIONS']['transaction_mode'] == 'IMMEDIATE'


def test_postgres_parameters_and_environment_credential(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_PASSWORD', 'test-secret')
    result = database_settings({'engine': 'postgresql', 'name': 'records', 'user': 'writer',
        'password_env': 'TEST_DATABASE_PASSWORD', 'host': 'db.internal', 'port': 5544,
        'options': {'sslmode': 'require'}}, tmp_path)
    assert result == {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'records', 'USER': 'writer',
        'PASSWORD': 'test-secret', 'HOST': 'db.internal', 'PORT': 5544, 'OPTIONS': {'sslmode': 'require'}}


@pytest.mark.parametrize('configuration', [{'engine': 'unknown'}, {'engine': 'postgres'}, {'engine': 'sqlite', 'unrecognized': True}])
def test_invalid_database_parameters_fail_before_connecting(tmp_path, configuration):
    with pytest.raises(ValueError):
        database_settings(configuration, tmp_path)
