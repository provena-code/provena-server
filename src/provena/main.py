import importlib
import pkgutil
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from provena.configs import api_config
import provena.api

app = FastAPI()

cors_config = api_config.cors_config

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_config.allow_origins,
    allow_credentials=cors_config.allow_credentials,
    allow_methods=cors_config.allow_methods,
    allow_headers=cors_config.allow_headers,
)

for module_info in pkgutil.walk_packages(provena.api.__path__, provena.api.__name__ + "."):
    print(f"Loading API module: {module_info.name}")
    module = importlib.import_module(module_info.name)
    if hasattr(module, "router"):
        app.include_router(module.router)
        print(f"Included router from {module_info.name}")


# TODO: Would be nice if CORS worked when there's an error
# But it seems like the headers don't get added here
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )
