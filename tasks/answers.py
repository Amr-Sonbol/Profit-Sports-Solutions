"""Answering a request without a visit: the reasons to choose from (grouped as
the form shows them), the decision each one records on the machine, and a
suggested answer to the customer for each — edited freely before sending."""
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from tasks.models import TicketMachine

Reason = TicketMachine.Reason
Decision = TicketMachine.Decision

COMPANY = 'Profit Sports Solutions'  # never translated

GROUPS = [
    (_('Resolved remotely'), Decision.ADVICE, [Reason.REMOTE_FIX, Reason.INSTRUCTIONS, Reason.CUSTOMER_FIX]),
    (_('Not covered'), Decision.NOT_COVERED,
     [Reason.OUT_OF_COVERAGE, Reason.MISUSE, Reason.NOT_OUR_EQUIPMENT, Reason.NO_PARTS]),
    (_('No action needed'), Decision.NO_ACTION, [Reason.DUPLICATE, Reason.ALREADY_RESOLVED, Reason.WITHDRAWN]),
]

# Earlier forms' values (a page left open), still understood.
_OLD = {'advice': Reason.REMOTE_FIX, 'not_covered': Reason.OUT_OF_COVERAGE, 'duplicate': Reason.DUPLICATE}

SUGGESTIONS = {
    Reason.REMOTE_FIX: _(
        'Thank you for contacting us. As discussed with your team, the issue was resolved remotely and the '
        'machine is working normally again. If the problem returns, please send us a new request.'
    ),
    Reason.INSTRUCTIONS: _(
        'Thank you for contacting us. We have shared the usage and maintenance instructions for this machine '
        'with your team. Following them should prevent the issue; if it continues, please send us a new request.'
    ),
    Reason.CUSTOMER_FIX: _(
        'Thank you for contacting us. The issue was resolved on site by your team (for example the power '
        'supply, a cable or a setting), so no visit is needed. If it happens again, please send us a new request.'
    ),
    Reason.OUT_OF_COVERAGE: _(
        'Thank you for contacting us. This machine is outside its warranty or service contract, so this repair '
        'is not covered. We would be glad to send you a quotation for the work.'
    ),
    Reason.MISUSE: _(
        'Thank you for contacting us. The damage reported comes from use or causes outside normal operation, '
        'which the warranty does not cover. We would be glad to send you a quotation for the repair.'
    ),
    Reason.NOT_OUR_EQUIPMENT: _(
        'Thank you for contacting us. This equipment was not supplied or serviced by %(company)s, so we are '
        'unable to take on this repair. We recommend contacting its supplier.'
    ),
    Reason.NO_PARTS: _(
        'Thank you for contacting us. Unfortunately the parts for this model are no longer manufactured, so a '
        'repair is not possible. Our team would be happy to advise you on a replacement.'
    ),
    Reason.DUPLICATE: _(
        'Thank you for contacting us. This request duplicates one we are already handling, so we have closed it '
        'to keep everything in one place. You can follow the progress on the original request.'
    ),
    Reason.ALREADY_RESOLVED: _(
        'Thank you for contacting us. We understand the issue has already been resolved, so we have closed this '
        'request. If you need anything further, please send us a new request.'
    ),
    Reason.WITHDRAWN: _(
        'As you asked, we have closed this request. If you need us again, please send a new request at any time.'
    ),
}


def reason_from(value):
    """The reason posted by the form (or an earlier form's value); None if unknown."""
    if value in Reason.values:
        return Reason(value)
    return _OLD.get(value)


def decision_for(reason):
    return next(decision for _label, decision, reasons in GROUPS if reason in reasons)


def form_groups(customer_language):
    """For the form: [(group label, [(value, label, suggested answer), …]), …] —
    the labels in the staff member's language, the answer in the customer's."""
    with translation.override(customer_language or 'en'):
        suggestions = {reason: str(text) % {'company': COMPANY} for reason, text in SUGGESTIONS.items()}
    return [
        (label, [(reason.value, reason.label, suggestions[reason]) for reason in reasons])
        for label, _decision, reasons in GROUPS
    ]
