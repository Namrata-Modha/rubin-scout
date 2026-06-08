#!/usr/bin/env bash
# Render build script
set -e

pip install -r requirements.txt

# Apply any pending database migrations before the new code goes live.
# 'set -e' above means the build fails fast if the migration errors,
# keeping Render from serving the new image against a stale schema.
alembic upgrade head
