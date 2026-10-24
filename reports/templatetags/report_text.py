import unicodedata

from django import template

register = template.Library()


@register.filter
def text_dir(value):
    """'rtl' when text starts (at its first letter) in a right-to-left
    script, else 'ltr'. The visit report PDF sets it on everything people
    type in — names, notes, findings — so an English customer name such
    as "24 Fitness" keeps its word order inside an Arabic report, and the
    other way round. WeasyPrint doesn't apply dir="auto" itself.
    """
    for char in str(value or ''):
        direction = unicodedata.bidirectional(char)
        if direction in ('R', 'AL'):
            return 'rtl'
        if direction == 'L':
            return 'ltr'
    return 'ltr'
