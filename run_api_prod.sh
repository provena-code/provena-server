#!/usr/bin/env bash
set -e

# Activate virtual environment
source ./venv/bin/activate

# Ensure uvicorn can resolve imports from ./src
export PYTHONPATH="$(pwd)/src"

# Run the app
exec uvicorn --app-dir ./src provena.main:app --port 5000 --root-path /provena
