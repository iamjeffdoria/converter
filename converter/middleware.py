import time
from django.contrib.auth import logout
from django.conf import settings

class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            last_activity = request.session.get('last_activity')
            timeout = getattr(settings, 'SESSION_INACTIVITY_TIMEOUT', 60)

            if last_activity and (time.time() - last_activity) > timeout:
                logout(request)
            else:
                request.session['last_activity'] = time.time()

        return self.get_response(request)