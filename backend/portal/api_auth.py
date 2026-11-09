"""Bearer credentials stored in the existing Django session table."""
from datetime import datetime, timedelta, timezone as dt_timezone
import uuid

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.dateparse import parse_datetime

TOKEN_PREFIX = 'gpa_'
TOKEN_SECONDS = 7 * 24 * 3600
MAX_TOKEN_SECONDS = 36500 * 24 * 3600
TOKEN_LIFETIMES = [(days * 86400, label) for days, label in
                   [(1, '1 天'), (7, '7 天'), (30, '30 天'), (90, '90 天'), (365, '1 年'), (0, '永久')]]


def token_id(key):
    return str(uuid.UUID(salted_hmac('api-token-id', key).hexdigest()[:32]))


def issue_token(user, expires_in=TOKEN_SECONDS, name=''):
    if type(expires_in) is not int or not 0 <= expires_in <= MAX_TOKEN_SECONDS:
        raise ValidationError(f'expires_in 须为 0–{MAX_TOKEN_SECONDS} 的整数秒数，0 表示永久。')
    if not isinstance(name, str) or len(name) > 100:
        raise ValidationError('令牌名称须为 100 字以内的文本。')
    created_at = timezone.now()
    expires_at = created_at + timedelta(seconds=expires_in) if expires_in else None
    session = SessionStore()
    session['api_user_id'] = user.pk
    session['api_auth_hash'] = user.get_session_auth_hash()
    session['api_name'] = name.strip() or 'API 令牌'
    session['api_created_at'] = created_at.isoformat()
    # The session table requires a date; API permanent credentials have no logical expiry.
    session.set_expiry(expires_at or datetime(9999, 12, 31, tzinfo=dt_timezone.utc))
    session.create()
    return {'id': token_id(session.session_key), 'name': session['api_name'], 'created_at': created_at,
            'token': TOKEN_PREFIX + session.session_key, 'token_type': 'Bearer',
            'expires_in': expires_in, 'expires_at': expires_at}


def _owned_sessions(user):
    # Signed session payloads require decoding. This scan is limited to token-management requests.
    for row in Session.objects.only('session_key', 'session_data', 'expire_date').iterator(chunk_size=256):
        data = row.get_decoded()
        if data.get('api_user_id') == user.pk:
            yield row, data


def list_tokens(user, *, include_secrets=False, current_key=None):
    now, auth_hash = timezone.now(), user.get_session_auth_hash()
    result = []
    for row, data in _owned_sessions(user):
        expires_at = None if row.expire_date.year == 9999 else row.expire_date
        valid = user.is_active and constant_time_compare(data.get('api_auth_hash', ''), auth_hash)
        status = 'expired' if row.expire_date <= now else 'active' if valid else 'invalidated'
        record = {'id': token_id(row.session_key), 'name': data.get('api_name') or 'API 令牌',
            'masked': TOKEN_PREFIX+'…'+row.session_key[-6:],
            'created_at': parse_datetime(data['api_created_at']) if data.get('api_created_at') else None,
            'expires_at': expires_at, 'status': status, 'current': row.session_key == current_key}
        if include_secrets:
            record['token'] = TOKEN_PREFIX+row.session_key
        result.append(record)
    return sorted(result, key=lambda row: (row['created_at'] is not None, row['created_at'] or now, row['id']), reverse=True)


def revoke_token(user, identifier):
    for row, data in _owned_sessions(user):
        if token_id(row.session_key) == str(identifier):
            row.delete()
            return True
    return False


def get_token(user, identifier, *, current_key=None):
    return next((row for row in list_tokens(user, include_secrets=True, current_key=current_key) if row['id'] == str(identifier)), None)


def bearer_user(request):
    header = request.headers.get('Authorization', '')
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.startswith(TOKEN_PREFIX):
        return None
    key = token.removeprefix(TOKEN_PREFIX)
    if len(key) != 32 or not key.isalnum():
        return None
    session = SessionStore(session_key=key)
    user_id = session.get('api_user_id')
    if user_id is None:
        return None
    user = get_user_model().objects.select_related('profile__group').filter(pk=user_id, is_active=True).first()
    if user is None or not constant_time_compare(session.get('api_auth_hash', ''), user.get_session_auth_hash()):
        session.delete()
        return None
    request.api_session = session
    return user
