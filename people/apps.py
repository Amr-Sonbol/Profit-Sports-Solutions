from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'people'

    def ready(self):
        # The audit trail: every change to the records that matter, and every sign-in (spots.audit).
        from spots.audit import connect
        connect()
