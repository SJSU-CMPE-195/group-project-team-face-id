#!/usr/bin/env bash
set -Eeuo pipefail

printf '%s\n' \
  'The unauthenticated standalone FaceID service has been retired.' \
  'Use: bash scripts/install-wireless-pi.sh' >&2
exit 1
