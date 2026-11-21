import hashlib
import re
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.db import transaction
from gpu_platform_control.slots import load
from .models import ExternalIdentity


def resolve_identity(identity):
    """Bind a verified provider identity to a persistent local account."""
    User = get_user_model()
    with transaction.atomic():
        binding = (
            ExternalIdentity.objects.select_related("user")
            .filter(provider=identity.provider, subject=identity.subject)
            .first()
        )
        if binding:
            return binding.user if binding.user.is_active else None
        if identity.provider == "local":
            user = User.objects.filter(pk=identity.subject, is_active=True).first()
            if user is None:
                return None
        else:
            # A provider identity never gains ownership of an account by matching its name.
            suffix = hashlib.sha256((identity.provider + "\x00" + identity.subject).encode()).hexdigest()[:12]
            from ..identity import RESERVED

            username = re.sub("[^a-z0-9_-]", "-", identity.username.lower()).strip("-")[:32]
            if not username or username[0].isdigit() or username in RESERVED:
                username = "user-" + suffix
            if User.objects.filter(username=username).exists():
                username = username[:19] + "-" + suffix
            user = User.objects.create_user(
                username=username, email=identity.email, first_name=identity.display_name[:150]
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
        ExternalIdentity.objects.create(provider=identity.provider, subject=identity.subject, user=user)
        return user


class SlotBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **credentials):
        from gpu_platform_control.slots.configuration import cluster_config

        configured = cluster_config().control.auth
        selection, options = configured.module, configured.options
        module = load("auth", selection, options=options)
        identity = module.authenticate(
            {"username": username or "", "password": password or "", **credentials}
        )
        return resolve_identity(identity) if identity else None

    def get_user(self, user_id):
        return get_user_model().objects.filter(pk=user_id, is_active=True).first()
