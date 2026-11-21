from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, override_settings

from gpu_platform_panel.views.pages import language


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_language_selection_sets_cookie_and_keeps_local_return_path():
    request = RequestFactory().post("/accounts/language/", {"language": "zh-hans", "next": "/tasks/"})
    request.user = SimpleNamespace(is_authenticated=False)
    response = language(request)
    assert response.status_code == 302
    assert response["Location"] == "/tasks/"
    assert response.cookies["django_language"].value == "zh-hans"


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_authenticated_selection_uses_public_service():
    request = RequestFactory().post("/accounts/language/", {"language": "en", "next": "https://external.invalid/path"})
    request.user = SimpleNamespace(is_authenticated=True)
    with patch("gpu_platform_control.services.set_language") as update:
        response = language(request)
    update.assert_called_once_with(request.user, "en")
    assert response["Location"] == "/"


def test_invalid_preference_has_no_cookie_or_persistence():
    request = RequestFactory().post("/accounts/language/", {"language": "unknown"})
    request.user = SimpleNamespace(is_authenticated=True)
    with patch("gpu_platform_control.services.set_language") as update:
        response = language(request)
    assert response.status_code == 400
    assert not response.cookies
    update.assert_not_called()


def test_language_changes_require_post():
    request = RequestFactory().get("/accounts/language/")
    request.user = SimpleNamespace(is_authenticated=False)
    assert language(request).status_code == 405
