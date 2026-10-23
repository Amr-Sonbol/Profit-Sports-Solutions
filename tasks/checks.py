"""Deploy-time checks (`manage.py check --deploy`)."""
from django.conf import settings
from django.core.checks import Warning, register

CONSOLE_EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


@register(deploy=True)
def email_is_really_sent(app_configs, **kwargs):
    """Every notification email — escalations, ticket confirmations,
    feedback requests — goes nowhere if production is still on the
    print-to-the-log backend that local development uses."""
    if settings.EMAIL_BACKEND == CONSOLE_EMAIL_BACKEND or not settings.EMAIL_HOST:
        return [Warning(
            'Emails are not being sent — they are only printed to the log.',
            hint='Set EMAIL_BACKEND (django.core.mail.backends.smtp.EmailBackend), EMAIL_HOST, '
                 'EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL in the environment.',
            id='tasks.W001',
        )]
    return []
