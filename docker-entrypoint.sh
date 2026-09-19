#!/bin/sh
set -e

until python manage.py check --database default >/dev/null 2>&1; do
  echo "Waiting for database at $DB_HOST:$DB_PORT..."
  sleep 1
done

python manage.py migrate --noinput

exec "$@"
