from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()
ANALYTICS_USERNAME = os.environ.get('ANALYTICS_USERNAME', 'admin')
ANALYTICS_PASSWORD = os.environ.get('ANALYTICS_PASSWORD')

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.contenttypes',
    'django.contrib.auth', 
    'django.contrib.sessions', 
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'converter',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
]


SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]
SOCIALACCOUNT_AUTO_SIGNUP = False

SOCIALACCOUNT_ADAPTER = 'converter.adapters.NoNewUsersGoogleAdapter'
ACCOUNT_ADAPTER = 'allauth.account.adapter.DefaultAccountAdapter'


ACCOUNT_EMAIL_VERIFICATION = 'none'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware', 
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',  # ADD
    'allauth.account.middleware.AccountMiddleware',
]

# Session engine — file-based, no DB needed
SESSION_ENGINE = 'django.contrib.sessions.backends.db' 
ROOT_URLCONF = 'mkv2mp4.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',      # ADD
                'django.contrib.messages.context_processors.messages',  # ADD
            ],
        },
    },
]

WSGI_APPLICATION = 'mkv2mp4.wsgi.application'

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATICFILES_DIRS = [BASE_DIR / 'mkv2mp4' / 'static']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Max upload size: 2GB
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024 * 1024

# Keep converted files for 1 hour, then clean up
CONVERTED_FILE_TTL_SECONDS = 3600

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ── FREE TIER LIMITS ──────────────────────────────────────────────────────────
FREE_MONTHLY_CONVERSIONS = 20
FREE_MAX_FILE_SIZE_MB    = 1536
PAID_MAX_FILE_SIZE_MB    = 2048  # 2GB for paid (change to 4096 for 4GB if you want)

# PayMongo
PAYMONGO_SECRET_KEY = os.environ.get('PAYMONGO_SECRET_KEY')  # from PayMongo dashboard
PAYMONGO_PUBLIC_KEY = os.environ.get('PAYMONGO_PUBLIC_KEY')
PAYMONGO_WEBHOOK_SECRET = os.environ.get('PAYMONGO_WEBHOOK_SECRET') # generated after Step 6

# Credit packs (must match your pricing.html)
CREDIT_PACKS = {
    'starter':  {'credits': 20,  'amount': 4900,  'name': 'Starter Pack'},   # amount in centavos
    'standard': {'credits': 50,  'amount': 9900,  'name': 'Standard Pack'},
    'pro':      {'credits': 120, 'amount': 19900, 'name': 'Pro Pack'},
}

FREE_MONTHLY_CONVERSIONS = 20
FREE_MAX_FILE_SIZE_MB    = 2048  # 2 GB
PAID_MAX_FILE_SIZE_MB    = 4096  # 4 GB

LOGIN_URL          = '/login/'
LOGIN_REDIRECT_URL = '/convert/'
LOGOUT_REDIRECT_URL = '/'

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "key": "",
            }
        ],
        "SCOPE": ["profile", "email", "openid"],
        "AUTH_PARAMS": {
            "access_type": "online",
        },
        "FETCH_USERINFO": True,
    }
}

GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

SOCIALACCOUNT_SIGNUP_FORM_CLASS = None  # uses allauth default
ACCOUNT_SIGNUP_FORM_CLASS = None

CSRF_TRUSTED_ORIGINS = [
    "https://converter-972n.onrender.com"
]

SOCIALACCOUNT_LOGIN_ON_GET = True