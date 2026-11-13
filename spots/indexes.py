from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db.models.functions import Upper


def contains_search_index(field, name):
    """A trigram index for searching inside a text column. Django's
    `icontains` runs as UPPER(column) LIKE '%…%', which a normal index
    can't help with; this index (on UPPER(column), so it matches that
    expression exactly) can, once the search has 3 or more characters.
    Needs the pg_trgm extension (customers migration 0011).
    """
    return GinIndex(OpClass(Upper(field), name='gin_trgm_ops'), name=name)


# ---- Searching names and phone numbers, as people type them ----

from django.db.models import CharField, Func, Lookup, TextField  # noqa: E402

# Arabic spelling variants written one way: hamza forms and alef wasla -> ا,
# ta marbuta -> ه, alef maksura -> ي. What follows them in FOLD_FROM (tatweel
# and the diacritics) has no counterpart in FOLD_TO, so TRANSLATE drops it.
FOLD_FROM = 'أإآٱةىـًٌٍَُِّْ'
FOLD_TO = 'ااااهي'
_FOLD_TABLE = str.maketrans({
    **{a: b for a, b in zip(FOLD_FROM, FOLD_TO)},
    **{a: None for a in FOLD_FROM[len(FOLD_TO):]},
})


def fold(text):
    """The search text in the same form as Folded makes of a column."""
    return str(text).upper().translate(_FOLD_TABLE)


class Folded(Func):
    """A column upper-cased, with Arabic spelling variants folded: "أحمد",
    "احمد" and "AHMED"/"ahmed" each read one way (fold, for the search text)."""
    function = 'TRANSLATE'
    template = "%(function)s(UPPER(%(expressions)s), '" + FOLD_FROM + "', '" + FOLD_TO + "')"
    output_field = CharField()


class PhoneDigits(Func):
    """A phone number's digits alone: "+971 50-123 4567" -> 971501234567."""
    function = 'REGEXP_REPLACE'
    template = "%(function)s(%(expressions)s, '[^0-9]', '', 'g')"
    output_field = CharField()


@CharField.register_lookup
@TextField.register_lookup
class Matches(Lookup):
    """`field__matches=text`: the text anywhere in the field, ignoring case and
    Arabic spelling variants (an index: matches_search_index)."""
    lookup_name = 'matches'
    prepare_rhs = False

    def as_sql(self, compiler, connection):
        lhs, params = compiler.compile(Folded(self.lhs))
        return f'{lhs} LIKE %s', [*params, f'%{connection.ops.prep_for_like_query(fold(self.rhs))}%']


@CharField.register_lookup
class Phone(Lookup):
    """`field__phone=text`: a phone number found by its digits whatever the
    format — spaces, dashes, a country code, a leading 0 ("050 123 4567"
    finds "+971501234567"). Fewer than four digits finds nothing (an index:
    phone_search_index)."""
    lookup_name = 'phone'
    prepare_rhs = False

    def as_sql(self, compiler, connection):
        digits = ''.join(ch for ch in str(self.rhs) if ch.isdigit()).lstrip('0')
        if len(digits) < 4:
            return 'FALSE', []
        lhs, params = compiler.compile(PhoneDigits(self.lhs))
        return f'{lhs} LIKE %s', [*params, f'%{digits}%']


def matches_search_index(field, name):
    """The trigram index for `field__matches` (on exactly Folded(field))."""
    return GinIndex(OpClass(Folded(field), name='gin_trgm_ops'), name=name)


def phone_search_index(field, name):
    """The trigram index for `field__phone` (on exactly PhoneDigits(field))."""
    return GinIndex(OpClass(PhoneDigits(field), name='gin_trgm_ops'), name=name)
