from django.utils.translation import gettext_lazy as tr
from django.core.exceptions import ValidationError
from django.db import transaction
from gpu_platform_control.core.accounts.models import UserProfile


@transaction.atomic
def set_language(user, language):
    if language not in {"en", "zh-hans", ""}:
        raise ValidationError(tr("Unsupported language."), code="unsupported_language")
    profile = UserProfile.objects.select_for_update().get(user=user)
    profile.language = language
    profile.save(update_fields=["language"])
    return {"language": language}
