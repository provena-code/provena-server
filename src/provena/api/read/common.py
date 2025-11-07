from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from provena.configs import  read_config

__db_reader_factory: SQLIOFactory = IOFactory.create_factory(read_config)

def create_reader():
    with __db_reader_factory.create_reader() as reader:
        yield reader
