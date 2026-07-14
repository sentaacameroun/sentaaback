import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "back_sentaa.settings")

app = Celery("back_sentaa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
