import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from gpu_platform_contracts.slots.auth import Identity
from gpu_platform_control.core.accounts.backend import resolve_identity
from gpu_platform_control.core.accounts.models import ExternalIdentity

pytestmark = pytest.mark.django_db


def test_provider_subject_is_stable_and_username_collision_isolated():
    existing = get_user_model().objects.create_user('reader', password='secret', is_staff=True)
    claim = Identity(provider='campus', subject='person-42', username='reader', display_name='Reader')
    created = resolve_identity(claim)
    assert created.pk != existing.pk
    assert not created.is_staff
    assert not created.has_usable_password()
    assert created.profile.group.is_default
    assert resolve_identity(claim).pk == created.pk
    assert ExternalIdentity.objects.count() == 1
    created.is_active = False
    created.save(update_fields=['is_active'])
    assert resolve_identity(claim) is None


def test_local_identity_binds_verified_account():
    existing = get_user_model().objects.create_user('reader')
    identity = Identity(provider='local', subject=str(existing.pk), username='reader')
    assert resolve_identity(identity).pk == existing.pk
    assert get_user_model().objects.count() == 1


def test_language_persists_and_redirect_stays_local():
    user = get_user_model().objects.create_user('reader')
    client = Client()
    client.force_login(user, backend='gpu_platform_control.core.accounts.backend.SlotBackend')
    response = client.post('/accounts/language/', {'language': 'zh-hans', 'next': 'https://elsewhere.invalid/'})
    user.profile.refresh_from_db()
    assert user.profile.language == 'zh-hans'
    assert response.status_code == 302
    assert response.url == '/'
    assert response.cookies['django_language'].value == 'zh-hans'


def test_anonymous_language_choice_uses_cookie():
    response = Client().post('/accounts/language/', {'language': 'en', 'next': '/accounts/login/'})
    assert response.status_code == 302
    assert response.cookies['django_language'].value == 'en'


def test_external_names_are_safe_environment_identities():
    from gpu_platform_contracts.environment import EnvironmentIdentity
    identity = Identity(provider='campus', subject='person-43', username='Student.Name@example.org')
    user = resolve_identity(identity)
    environment = EnvironmentIdentity(username=user.username, uid=user.profile.uid, gid=user.profile.gid)
    assert environment.username == user.username
    assert resolve_identity(identity).pk == user.pk
    assert len(user.username) <= 32


def test_api_explicit_language_ignores_browser_language_cookie():
    from django.test import RequestFactory
    from django.contrib.auth.models import AnonymousUser
    from gpu_platform_control.core.accounts.middleware import UserLocaleMiddleware
    request = RequestFactory().get('/api/v1/resources', HTTP_ACCEPT_LANGUAGE='en')
    request.COOKIES['django_language'] = 'zh-hans'
    request.user = AnonymousUser()
    UserLocaleMiddleware(lambda _: None).process_request(request)
    assert request.LANGUAGE_CODE == 'en'
    assert request.COOKIES['django_language'] == 'zh-hans'
