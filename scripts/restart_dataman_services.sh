#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT_DIR/scripts/stop_dataman_services.sh"
"$ROOT_DIR/scripts/start_dataman_services.sh"
