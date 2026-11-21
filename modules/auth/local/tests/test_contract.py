import pytest
from django.contrib.auth import get_user_model

from gpu_platform_auth_local.module import Module
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import AuthContract

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth(tmp_path):
    get_user_model().objects.create_user("member", password="fixture-passphrase")
    return Module(ModuleContext(node_id="control", state_directory=tmp_path))


@pytest.fixture
def valid_credentials():
    return {"username": "member", "password": "fixture-passphrase"}


@pytest.fixture
def invalid_credentials():
    return {"username": "member", "password": "incorrect"}


class TestLocalAuthentication(AuthContract):
    pass


def test_disabled_account_does_not_authenticate(auth, valid_credentials):
    get_user_model().objects.filter(username="member").update(is_active=False)
    assert auth.authenticate(valid_credentials) is None


def test_identity_is_stable_when_display_name_changes(auth, valid_credentials):
    first = auth.authenticate(valid_credentials)
    get_user_model().objects.filter(username="member").update(username="renamed")
    second = auth.authenticate({**valid_credentials, "username": "renamed"})
    assert second.subject == first.subject
    assert second.username == "renamed"


def test_local_authentication_rejects_unsupported_configuration():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Module.validate_options({"authentication_url": "https://identity.example"})
