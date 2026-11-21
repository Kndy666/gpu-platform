from django.middleware.locale import LocaleMiddleware
from django.utils import translation
from copy import copy


class UserLocaleMiddleware(LocaleMiddleware):
    def process_request(self, request):
        super().process_request(request)
        if request.path.startswith("/api/"):
            negotiation = copy(request)
            negotiation.COOKIES = {}
            language = (
                translation.get_language_from_request(negotiation)
                if request.headers.get("Accept-Language")
                else "en"
            )
        elif request.user.is_authenticated:
            language = request.user.profile.language or request.LANGUAGE_CODE
        else:
            language = request.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = language
