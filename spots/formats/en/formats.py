# Day-month-year everywhere, in both languages — this is how dates are
# read here, not the US month-first convention Django's own 'en' locale
# defaults to. See spots/settings.py FORMAT_MODULE_PATH.
DATE_FORMAT = 'j F Y'
SHORT_DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'j F Y, P'
SHORT_DATETIME_FORMAT = 'd/m/Y P'
