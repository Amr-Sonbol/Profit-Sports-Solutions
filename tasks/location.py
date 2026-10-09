"""Where the phone was at each technician tap, checked against the site
(docs: task_event, site). Location is taken only at the tap itself —
never tracked in between — and a tap far from the site still counts; it's
just flagged for a supervisor to ask about.
"""
import math
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .models import TaskEvent

# A tap within this distance of the site counts as at the site, plus the
# phone's own reported accuracy (capped, so a wildly vague fix can't
# excuse anything).
AT_SITE_METRES = 300
MAX_ACCURACY_ALLOWANCE_METRES = 200

# Taps that should happen at the site — the only ones flagged "Not at the
# site". Accepting and "on the way" are naturally elsewhere; their
# location is still kept, just never flagged.
ON_SITE_TAPS = {
    TaskEvent.EventType.ARRIVED, TaskEvent.EventType.STARTED, TaskEvent.EventType.PAUSED,
    TaskEvent.EventType.RESUMED, TaskEvent.EventType.REPORT_SUBMITTED,
}


def distance_metres(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points, in whole metres."""
    lat1, lng1, lat2, lng2 = (math.radians(float(value)) for value in (lat1, lng1, lat2, lng2))
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return round(6_371_000 * 2 * math.asin(math.sqrt(a)))


def _coordinate(value, limit):
    try:
        number = Decimal(str(value)).quantize(Decimal('0.000001'))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if -limit <= number <= limit else None


def parse_coordinates(text):
    """(latitude, longitude) from "25.07, 55.13" or a Google Maps link
    that has the coordinates in it (…/@25.07,55.13,17z, ?q=25.07,55.13,
    or …!3d25.07!4d55.13). None if there are none to find — a short
    maps.app.goo.gl link hides them, so paste the full one or the numbers.
    """
    text = (text or '').strip()
    match = (
        re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', text)
        or re.search(r'@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)', text)
        or re.search(r'(?:^|[?&](?:q|query|ll)=)(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)', text)
    )
    if not match:
        return None
    latitude, longitude = _coordinate(match.group(1), 90), _coordinate(match.group(2), 180)
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def tap_location_fields(task, event_type, data):
    """The location fields for a new TaskEvent, from the `latitude`,
    `longitude` and `accuracy` the app (or the web page) sent with the tap.
    The first "Arrived" at a site nobody has located yet sets the site's
    location — the office can correct it later.
    """
    latitude = _coordinate(data.get('latitude'), 90) if data.get('latitude') not in (None, '') else None
    longitude = _coordinate(data.get('longitude'), 180) if data.get('longitude') not in (None, '') else None
    if latitude is None or longitude is None:
        return {'location_status': TaskEvent.LocationStatus.NO_LOCATION}

    try:
        accuracy = max(0, min(int(float(data.get('accuracy') or 0)), 100_000)) or None
    except (TypeError, ValueError):
        accuracy = None
    fields = {'latitude': latitude, 'longitude': longitude, 'location_accuracy_m': accuracy}

    site = task.site
    if site.latitude is None or site.longitude is None:
        if event_type == TaskEvent.EventType.ARRIVED:
            site.latitude, site.longitude = latitude, longitude
            site.location_source = site.LocationSource.ARRIVAL
            site.save(update_fields=['latitude', 'longitude', 'location_source'])
            return {**fields, 'distance_m': 0, 'location_status': TaskEvent.LocationStatus.AT_SITE}
        return {**fields, 'location_status': TaskEvent.LocationStatus.SITE_UNKNOWN}

    distance = distance_metres(latitude, longitude, site.latitude, site.longitude)
    allowance = AT_SITE_METRES + min(accuracy or 0, MAX_ACCURACY_ALLOWANCE_METRES)
    if event_type not in ON_SITE_TAPS:
        status = ''
    elif distance <= allowance:
        status = TaskEvent.LocationStatus.AT_SITE
    else:
        status = TaskEvent.LocationStatus.AWAY
    return {**fields, 'distance_m': distance, 'location_status': status}


def record_tap(task, event_type, actor, data, note='', occurred_at=None):
    """Creates the TaskEvent for a technician's tap, with where it happened."""
    return TaskEvent.objects.create(
        task=task, event_type=event_type, occurred_at=occurred_at or timezone.now(), actor=actor, note=note,
        **tap_location_fields(task, event_type, data),
    )
