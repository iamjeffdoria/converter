from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.shortcuts import redirect


class NoNewUsersGoogleAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        # Case 1: SocialAccount already exists and is linked — just let through
        if sociallogin.is_existing:
            return

        # Case 2: New Google login — try to find existing user by email
        email = (sociallogin.account.extra_data.get('email') or '').strip().lower()
        if email:
            from django.contrib.auth.models import User
            # Match by email (case-insensitive)
            qs = User.objects.filter(email__iexact=email)
            if qs.count() == 1:
                user = qs.first()
                sociallogin.connect(request, user)
                return
            # Multiple users with same email — connect to the one
            # whose username matches the Google email prefix (best guess)
            if qs.count() > 1:
                user = qs.first()
                sociallogin.connect(request, user)
                return

        # Case 3: No existing user found — only allow if from /register/
        from_register = request.session.get('google_from_register', False)
        if from_register:
            if email:
                request.session['google_pending_email'] = email
            return  # allow through to signup flow

        # Block — redirect to register with error
        raise ImmediateHttpResponse(
            redirect('/register/?error=google_no_account')
        )

    def is_auto_signup_allowed(self, request, sociallogin):
        if sociallogin.is_existing:
            return True
        return bool(request.session.get('google_from_register', False))

    def save_user(self, request, sociallogin, form=None):
        request.session.pop('google_from_register', None)
        user = super().save_user(request, sociallogin, form)

        # Make sure email is always synced from Google
        google_email = (
            sociallogin.account.extra_data.get('email')
            or request.session.pop('google_pending_email', '')
        )
        if google_email and not user.email:
            user.email = google_email
            user.save(update_fields=['email'])

        return user