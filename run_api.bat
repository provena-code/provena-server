set PROVENA_ALLOW_INSECURE_COOKIES=1
set PYTHONPATH=./src && uvicorn --app-dir ./src provena.main:app --reload --reload-dir ./src/ --port 8001
