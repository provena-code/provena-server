import logging

from MySQLdb import OperationalError
logger = logging.getLogger(__name__)

import importlib
import pkgutil
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from provena.api.logging.logging import add_error_event, add_malformatted_events
from provena.configs import api_config
import provena.api

# Set python's logging level to uvicorns if uvicorn is being used
if "uvicorn" in logging.Logger.manager.loggerDict:
    uvicorn_logger = logging.getLogger("uvicorn")
    logging.basicConfig(level=uvicorn_logger.level)

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
    # logger.info(f"Loading API module: {module_info.name}")
    module = importlib.import_module(module_info.name)
    if hasattr(module, "router"):
        app.include_router(module.router)
        # logger.info(f"Included router from {module_info.name}")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Log the detailed error information
    exc_str = f"Request validation error: {exc}".replace('\n', ' ').replace('  ', ' ')
    # TODO: Actual logging!
    logger.error(f"Validation error: {exc_str}")

    # Get the route that would have been called
    route = request.scope.get("route")
    if route:
        path = route.path if route else request.url.path
        if path == "/events":
            # Get the body of the request
            try:
                body = await request.json()
                if isinstance(body, list):
                    result = add_malformatted_events(body)
                    result.errors.insert(0, f"Some events were malformatted and could not be parsed correctly: {exc_str}")
                    return JSONResponse(
                        content=jsonable_encoder(result),
                    )
            except Exception as e:
                logger.info(f"Error reading request body: {e}")
            try:
                text = await request.body()
                text = text.decode("utf-8")
                result = add_error_event(f"Could not parse request body.", text)
                result.errors.insert(0, f"Content was not valid JSON: {exc_str}")
                return JSONResponse(
                    content=jsonable_encoder(result),
                )
            except Exception as e:
                logger.info(f"Error reading request body for logging: {e}")

    return await request_validation_exception_handler(request, exc)

@app.exception_handler(OperationalError)
async def db_handler(request: Request, exc: OperationalError):
    logger.error(f"Database operational error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Database Operational Error"},
    )

# TODO: Would be nice if CORS worked when there's an error
# But it seems like the headers don't get added here
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    try:
        request = None
        try:
            request = await request.body()
            request = request.decode("utf-8")
        except Exception as e:
            pass
        add_error_event(f"Internal server error: {exc}", request)
    except Exception as e:
        logger.error(f"Error logging internal server error: {e}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )
