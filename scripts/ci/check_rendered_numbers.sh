#!/bin/sh
# Fail when a rendered manifest carries a number in exponent form.
#
# Helm reads YAML numbers as floats, so `{{ .Values.x | quote }}` renders 2000000 as "2e+06",
# which the service refuses as an integer setting. Templates must write `| int64 | quote`.
set -eu

[ "$#" -gt 0 ] || { echo "usage: $0 <rendered manifest>..." >&2; exit 2; }

status=0
for manifest in "$@"; do
  [ -s "${manifest}" ] || { echo "${manifest}: missing or empty" >&2; exit 2; }
  if grep -nE "(^|[[\"' :,])[0-9]+(\.[0-9]+)?e\+[0-9]+(\$|[]\"' ,}])" "${manifest}"; then
    echo "${manifest}: a number was rendered in exponent form" >&2
    status=1
  fi
done
exit "${status}"
