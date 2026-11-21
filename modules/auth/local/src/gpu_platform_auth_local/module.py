from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.auth import Auth, Identity


class Options(Contract):
    """Local authentication uses the control center's Django account database."""


class Module(Auth):
    options_model = Options
    descriptor = ModuleDescriptor(name="local", slot="auth", version="0.4.0", side="control",
                                  parameters=Options.model_json_schema())

    def authenticate(self, credentials):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.hashers import make_password

        username = credentials.get("username", "")
        password = credentials.get("password", "")
        model = get_user_model()
        try:
            user = model._default_manager.get_by_natural_key(username)
        except model.DoesNotExist:
            make_password(password)
            return None
        if not user.check_password(password) or not user.is_active:
            return None
        return Identity(
            subject=str(user.pk),
            provider="local",
            username=user.get_username(),
            email=user.email,
            display_name=user.get_full_name(),
        )
