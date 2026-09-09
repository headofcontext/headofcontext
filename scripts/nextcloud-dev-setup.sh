#!/usr/bin/env bash
# One-time preparation of the local Nextcloud container before scripts/load_nextcloud.py.
#
# Nextcloud rate-limits share creation to 20 per 10 minutes per user
# (UserRateLimit on ShareAPIController::createShare). The supported bypass is the brute-force
# allow list applied to rate limiting, so the loader's ~700 shares go through in one run.
set -euo pipefail
cd "$(dirname "$0")/.."
occ() { docker compose exec -T -u www-data nextcloud php occ "$@"; }
occ config:app:set bruteforcesettings apply_allowlist_to_ratelimit --value true >/dev/null
occ config:app:set bruteForce whitelist_0 --value 192.168.0.0/16 >/dev/null
occ config:app:set bruteForce whitelist_1 --value 172.16.0.0/12 >/dev/null
occ config:app:set bruteForce whitelist_2 --value 10.0.0.0/8 >/dev/null
echo "nextcloud: rate limiting bypassed for private networks (dev only)"
