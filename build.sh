#!/usr/bin/env bash
# Builds the "Evidence comparison" Creation of an Evidence Desk project.
# Möbius runs this reviewed script from the project root with PROJECT_ROOT,
# PROJECT_SOURCE, PROJECT_OUTPUT_DIR and PROJECT_ARTIFACT_ID set. It runs on
# the platform's Python, not the app's environment, so the builder imports no
# third-party packages. No bytecode is written into the installed app source.
set -euo pipefail
: "${PROJECT_ROOT:?}" "${PROJECT_OUTPUT_DIR:?}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$(dirname "$0")/build_view.py"
