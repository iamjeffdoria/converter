from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.shortcuts import redirect


class NoNewUsersGoogleAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        # Case 1: SocialAccount already linked — let through immediately
        if sociallogin.is_existing:
            return

        from django.contrib.auth.models import User
        from allauth.socialaccount.models import SocialAccount

        google_uid = sociallogin.account.uid

        # Case 2: SocialAccount exists in DB but allauth didn't detect it
        # (can happen on free-tier cold starts / session loss)
        try:
            existing_social = SocialAccount.objects.get(
                provider='google', uid=google_uid
            )
            sociallogin.connect(request, existing_social.user)
            return
        except SocialAccount.DoesNotExist:
            pass

        # Case 3: No SocialAccount yet — try matching by email
        email = (sociallogin.account.extra_data.get('email') or '').strip().lower()
        if email:
            qs = User.objects.filter(email__iexact=email)
            if qs.exists():
                user = qs.first()
                sociallogin.connect(request, user)
                return

        # Case 4: No match at all — only allow if coming from /register/
        from_register = request.session.get('google_from_register', False)
        if from_register:
            if email:
                request.session['google_pending_email'] = email
            return  # allow through to signup flow

        # Block — not registered, not from register page
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

        google_email = (
            sociallogin.account.extra_data.get('email')
            or request.session.pop('google_pending_email', '')
        )
        if google_email and not user.email:
            user.email = google_email
            user.save(update_fields=['email'])

        # Always create UserAccount for Google-registered users
        from .models import UserAccount
        visitor_id = request.COOKIES.get('vc_visitor_id', '')
        UserAccount.objects.get_or_create(
            user=user,
            defaults={'visitor_id': visitor_id}
        )

        return user