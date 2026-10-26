from django.utils import translation

from users.bootstrap import provision_default_users


class RootProvisioningMiddleware:
    """Synchronize env-managed demo users before the first request."""

    provisioned = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not type(self).provisioned:
            provision_default_users()
            type(self).provisioned = True
        return self.get_response(request)


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            translation.activate(request.user.language)
            request.LANGUAGE_CODE = request.user.language
        return self.get_response(request)
