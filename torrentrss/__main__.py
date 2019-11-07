import json
import asyncio
from contextlib import closing
from argparse import ArgumentParser, Namespace
from typing import Literal, Union

from . import logging
from .torrentrss import TorrentRSS
from .constants import VERSION, CONFIG_PATH, CONFIG_SCHEMA
from .utils import show_exception_notification


class CommandLineArguments(Namespace):
    logging_level: Union[
        Literal['DISABLE'],
        Literal['DEBUG'],
        Literal['INFO'],
        Literal['WARNING'],
        Literal['ERROR'],
        Literal['CRITICAL']
    ]
    schema: bool
    version: bool


async def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        '-l', '--logging-level',
        default='DEBUG',
        choices=['DISABLE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    )
    parser.add_argument(
        '-s', '--schema',
        action='store_true'
    )
    parser.add_argument(
        '-v', '--version',
        action='store_true'
    )
    arguments = parser.parse_args(namespace=CommandLineArguments())

    if arguments.version:
        print(VERSION)
        return

    if arguments.schema:
        schema = json.dumps(CONFIG_SCHEMA, indent=2, sort_keys=False)
        print(schema)
        return

    logging.configure(level=arguments.logging_level)

    app: TorrentRSS
    try:
        app = await TorrentRSS.from_path()
    except FileNotFoundError:
        message = f'No config file found at {str(CONFIG_PATH)!r}. ' \
            + "See '--schema' for reference."
        parser.error(message)

    try:
        await app.run()
    except Exception as error:
        await logging.exception(error.__class__.__name__)
        await show_exception_notification(error)
        parser.exit(2)


# asyncio.run doesn't seem to play nicely with how argparse calls sys.exit,
# so we start our own event loop instead
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
with closing(loop):
    loop.run_until_complete(main())
