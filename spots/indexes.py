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
