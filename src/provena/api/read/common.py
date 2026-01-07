from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from provena.configs import  read_config, api_config
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

__db_reader_factory: SQLIOFactory = IOFactory.create_factory(read_config)

def create_reader():
    with __db_reader_factory.create_reader() as reader:
        yield reader


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# TODO: This is a temporary solution for API key checking until
# we get user roles and authentication set up.
def require_api_key(api_key: str = Security(api_key_header)):
    if api_key not in api_config.testing_api_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )