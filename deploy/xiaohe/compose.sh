#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
VERSION="$(tr -d '\r\n' < VERSION)"
export VERSION
exec docker compose -f deploy/xiaohe/compose.yml "$@"
