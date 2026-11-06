"""The visit report's standard wording — each phrase written once, in
English here and in Arabic in the translations, so a report reads fully
in its customer's language however the technician filled it in (Arabic in
Saudi Arabia and Egypt, English elsewhere; reports.pdf.report_language).
The technician picks these from lists; anything else they type, in the
report's language. Stored as codes, never as text, so a report shows in
either language. Kept free of model imports: tasks.models uses them too.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class WorkLine(models.TextChoices):
    """Work performed on the visit as a whole."""

    TESTED = 'tested', _('All machines tested to confirm proper operation.')
    BOLTS = 'bolts', _('All bolts checked and tightened.')
    CABLES = 'cables', _('All cables inspected and confirmed in good condition.')
    GREASED = 'greased', _('All machines greased.')
    SILICONE = 'silicone', _('Silicone spray applied to all machines.')
    CLEANED = 'cleaned', _('All machines cleaned.')
    BELTS = 'belts', _('Running belts inspected, aligned and tensioned.')
    ELECTRICAL = 'electrical', _('Electrical connections and power supply checked.')
    CALIBRATED = 'calibrated', _('Machines calibrated.')
    UPHOLSTERY = 'upholstery', _('Upholstery and grips inspected.')


class Finding(models.TextChoices):
    """What was found on one machine."""

    NOISE_MOVING_PART = 'noise_moving_part', _('Noise from a moving part')
    NOISE_UNDER_LOAD = 'noise_under_load', _('Noise under heavy weights')
    DISPLAY = 'display', _('Display or console fault')
    BELT = 'belt', _('Worn or slipping belt')
    CABLE = 'cable', _('Worn or frayed cable')
    LOOSE = 'loose', _('Loose bolts or parts')
    POWER = 'power', _('No power or an electrical fault')
    UPHOLSTERY = 'upholstery', _('Damaged upholstery')
    NO_FAULT = 'no_fault', _('No fault found')


class Action(models.TextChoices):
    """What was done about it."""

    ADJUSTED = 'adjusted', _('Part inspected and adjusted')
    GREASED = 'greased', _('Machine disassembled and the affected part greased')
    TIGHTENED = 'tightened', _('Bolts tightened')
    PART_REPLACED = 'part_replaced', _('Faulty part replaced')
    CABLE_REPLACED = 'cable_replaced', _('Cable replaced')
    BELT_REPLACED = 'belt_replaced', _('Belt replaced')
    BELT_ADJUSTED = 'belt_adjusted', _('Belt aligned and tensioned')
    CONSOLE_REPLACED = 'console_replaced', _('Console replaced')
    CALIBRATED = 'calibrated', _('Machine calibrated')
    PART_ORDERED = 'part_ordered', _('Part ordered, to be fitted on a follow-up visit')
    NONE_NEEDED = 'none_needed', _('No action needed')


class Recommendation(models.TextChoices):
    """Advice to the customer."""

    KEEP_GREASE = 'keep_grease', _(
        "To maintain the lubrication, we recommend advising the gym's cleaning team not to remove the grease "
        'applied to the moving parts during routine cleaning.'
    )
    REGULAR_CLEANING = 'regular_cleaning', _(
        'We recommend cleaning the equipment regularly with suitable, non-abrasive products.'
    )
    WEIGHT_LIMITS = 'weight_limits', _("We recommend that members respect each machine's maximum load.")
    REPLACE_SOON = 'replace_soon', _('We recommend replacing the parts noted above at the next visit.')
    OUT_OF_USE = 'out_of_use', _('We recommend keeping the machines noted above out of use until they are repaired.')
    REGULAR_PPM = 'regular_ppm', _('We recommend keeping to the scheduled preventive maintenance visits.')
