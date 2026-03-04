import os
from pathlib import Path

# import cloudinary
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = True

SECRET_KEY = os.getenv("SECRET_KEY")

# MAINTENANCE_MODE = int(os.getenv("MAINTENANCE_MODE"))

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS").split(",")

MEDIA_ROOT = os.getenv("MEDIA_ROOT")
MEDIA_URL = "/media/"

STATIC_URL = "/static/"
STATIC_ROOT = os.getenv("STATIC_ROOT")


GOOGLE_RECAPTCHA_SITE_KEY = os.getenv("GOOGLE_RECAPTCHA_SITE_KEY")
GOOGLE_RECAPTCHA_SECRET_KEY = os.getenv("GOOGLE_RECAPTCHA_SECRET_KEY")

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")

# cloudinary.config(
#     cloud_name=os.getenv("CLOUDINARY_USER"),
#     api_key=os.getenv("CLOUDINARY_API_KEY"),
#     api_secret=os.getenv("CLOUDINARY_API_SECRET"),
#     secure=True,
# )

# CORS CONF
CACHE_MIDDLEWARE_SECONDS = 300
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS").split(",")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS").split(",")
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-auth-token",
]

# DATABASE CACHE CONFIGURATION
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
        "TIMEOUT": 600,
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}

ADMINS = [("""CEO""", os.getenv("CEO_EMAIL"))]
# https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.cache.UpdateCacheMiddleware",
    "django.middleware.cache.FetchFromCacheMiddleware",
    "django.middleware.locale.LocaleMiddleware",
]


# JOURNALISATION DES LOGS
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": {
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        },
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django.request": {
            "handlers": ["mail_admins"],
            "level": "ERROR",
            "propagate": True,
            "filename": os.getenv("LOG_DIR"),
        },
        "django.security.DisallowedHost": {
            "level": "ERROR",
            "handlers": ["console", "mail_admins"],
            "propagate": True,
        },
    },
}

# https settings
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = True


# sessions config https://docs.djangoproject.com/en/3.0/ref/settings/#settings-sessions
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 259200
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SAMESITE = "None"

# hsts settings
SECURE_HSTS_SECONDS = 31536000  # 1 YEAR
SECURE_HSTS_PRELOAD = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True


EMAIL_USE_TLS = False
EMAIL_USE_SSL = True
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = os.getenv("EMAIL_PORT")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
FRONT_URI = "https://dev-newsite-mnlvm.netlify.app"

EMAIL_EURO_MILLION_MONDE = os.getenv("EMAIL_EURO_MILLION_MONDE")
RECEIVER_MAIL_MESSAGE = os.getenv("RECEIVER_MAIL_MESSAGE")
EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
RECEIVER_MAIL = os.getenv("RECEIVER_MAIL")
HOSTED_BUTTON_ID = os.getenv("HOSTED_BUTTON_ID")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("WEBSITE_POSTGRES_DB"),
        "USER": os.getenv("WEBSITE_POSTGRES_USER"),
        "PASSWORD": os.getenv("WEBSITE_POSTGRES_PASS"),
        "HOST": os.getenv("WEBSITE_POSTGRES_HOST"),
        "PORT": os.getenv("WEBSITE_POSTGRES_PORT"),
        "CONN_MAX_AGE": 60,
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "EXCEPTION_HANDLER": "ekila.exception_handler.exceptions_handler",
    "PAGE_SIZE": 10,
}
