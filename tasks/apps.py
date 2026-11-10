from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'

    def ready(self):
        from . import checks  # noqa: F401 — registers the deploy checks
        # Every action on a task tells the people on it (tasks.updates).
        from django.db.models.signals import post_save

        from .models import TaskEvent
        from .updates import on_event_saved
        post_save.connect(on_event_saved, sender=TaskEvent, dispatch_uid='task_event_tells_the_team')
