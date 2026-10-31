#!/bin/sh
set -eu

container="${1:?container name is required}"
database_user="${2:?database user is required}"
database_name="${3:?database name is required}"
max_attempts="${POSTGRES_READY_ATTEMPTS:-60}"
sleep_seconds="${POSTGRES_READY_SLEEP_SECONDS:-1}"

case "${max_attempts}" in
  ''|*[!0-9]*|0)
    echo "POSTGRES_READY_ATTEMPTS must be a positive integer" >&2
    exit 2
    ;;
esac

attempt=1
while [ "${attempt}" -le "${max_attempts}" ]; do
  if docker exec "${container}" psql --no-psqlrc --no-password \
    --set ON_ERROR_STOP=1 --tuples-only --no-align \
    --username "${database_user}" --dbname "${database_name}" \
    --command "SELECT 1;" 2>/dev/null | grep -qx '1'; then
    exit 0
  fi

  echo "Waiting for PostgreSQL database ${database_name} (${attempt}/${max_attempts})" >&2
  attempt=$((attempt + 1))
  if [ "${attempt}" -le "${max_attempts}" ]; then
    sleep "${sleep_seconds}"
  fi
done

echo "PostgreSQL database ${database_name} did not become queryable" >&2
docker logs --tail 100 "${container}" >&2 || true
exit 1
