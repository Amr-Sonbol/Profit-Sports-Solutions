"""At most one of something in a while — the shared database cache
(settings.CACHES), so every server process sees the same limit and it
survives a restart.

Used where anyone, signed in or not, can make us send an email: the
"Forgot your password?" pages (the portal's and the staff one). Without a
limit, someone could flood a person's inbox — and use up the mail
account's daily sending allowance, which would stop every email the app
sends that day.
"""
from django.core.cache import cache

# One emailed sign-in link per address in this time; asking again sooner
# gets the same answer on the page, and nothing is sent.
LINK_EMAIL_EVERY = 15 * 60  # seconds


def first_in_a_while(key, seconds=LINK_EMAIL_EVERY):
    """True the first time `key` is asked for within `seconds`, False after."""
    return cache.add(f'throttle:{key}', True, seconds)


def link_email_allowed(email):
    """Whether a sign-in link may be emailed to this address now (one per LINK_EMAIL_EVERY)."""
    return first_in_a_while(f'link-email:{email.strip().lower()}')
