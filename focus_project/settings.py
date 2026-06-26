import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default

    normalized_value = value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    return default


def env_list(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default or []

    return [item.strip() for item in value.split(",") if item.strip()]


def oauth_provider(name, label, authorize_url, token_url, profile_url, scope, extra=None):
    client_id = os.environ.get(f"FOCUS_{name}_CLIENT_ID", "")
    client_secret = os.environ.get(f"FOCUS_{name}_CLIENT_SECRET", "")
    provider = {
        "label": label,
        "client_id": client_id,
        "client_secret": client_secret,
        "authorize_url": authorize_url,
        "token_url": token_url,
        "profile_url": profile_url,
        "scope": scope,
    }
    if extra:
        provider.update(extra)
    return provider


DEFAULT_INSECURE_SECRET_KEY = "django-insecure-local-development-key"
SECRET_KEY = os.environ.get("FOCUS_SECRET_KEY", DEFAULT_INSECURE_SECRET_KEY)
DEBUG = env_bool("FOCUS_DEBUG", True)
ALLOWED_HOSTS = env_list("FOCUS_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("FOCUS_CSRF_TRUSTED_ORIGINS")

SESSION_COOKIE_SECURE = env_bool("FOCUS_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("FOCUS_CSRF_COOKIE_SECURE", not DEBUG)
SECURE_SSL_REDIRECT = env_bool("FOCUS_SECURE_SSL_REDIRECT", not DEBUG)
if env_bool("FOCUS_SECURE_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

FOCUS_OAUTH_PROVIDERS = {
    "GITHUB": oauth_provider(
        "GITHUB",
        "GitHub",
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        "https://api.github.com/user",
        "read:user",
    ),
    "DISCORD": oauth_provider(
        "DISCORD",
        "Discord",
        "https://discord.com/oauth2/authorize",
        "https://discord.com/api/oauth2/token",
        "https://discord.com/api/users/@me",
        "identify",
    ),
}
FOCUS_ENABLE_MASTODON_SIGN_IN = env_bool("FOCUS_ENABLE_MASTODON_SIGN_IN", True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "focus_core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "focus_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "focus_core.context_processors.unread_notification_count",
            ],
        },
    },
]

WSGI_APPLICATION = "focus_project.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_USER_MODEL = "focus_core.FocusUser"

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FOCUS_ENABLE_DEV_SIGN_IN = env_bool("FOCUS_ENABLE_DEV_SIGN_IN", DEBUG)
FOCUS_LOGIN_VIEW = os.environ.get("FOCUS_LOGIN_VIEW", "dev_sign_in" if FOCUS_ENABLE_DEV_SIGN_IN else "passkey_sign_in")

LOGIN_URL = FOCUS_LOGIN_VIEW
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = FOCUS_LOGIN_VIEW
