#!/bin/sh
set -e

until python manage.py check --database default >/dev/null 2>&1; do
  echo "Waiting for database at $DB_HOST:$DB_PORT..."
  sleep 1
done

python manage.py migrate --noinput
# The database cache's table (settings.CACHES); does nothing once it exists.
python manage.py createcachetable

# Dev serves STATICFILES_DIRS on the fly (DEBUG=True) and bind-mounts the
# repo, where a collected staticfiles/ folder would just be clutter —
# only production (nginx serving STATIC_ROOT directly) needs this.
if [ "$DEBUG" != "True" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
