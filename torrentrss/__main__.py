import json
import asyncio
from argparse import ArgumentParser

from .torrentrss import TorrentRSS
from .constants import VERSION, CONFIG_PATH, CONFIG_SCHEMA
from .logging import configure_logging, logger
from .utils import show_exception_notification


async def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        '-l', '--logging-level',
        default='DEBUG',
        choices=['DISABLE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    )
    parser.add_argument(
        '-p', '--print-config-schema',
        action='store_true'
    )
    parser.add_argument(
        '-v', '--version',
        action='store_true'
    )
    arguments = parser.parse_args()

    if arguments.version:
        print(VERSION)
        return

    if arguments.print_config_schema:
        schema = json.dumps(CONFIG_SCHEMA, indent=4, sort_keys=False)
        print(schema)
        return

    configure_logging(level=arguments.logging_level)

    app: TorrentRSS
    try:
        app = await TorrentRSS.from_path()
    except FileNotFoundError:
        parser.error(
            f'No config file found at {str(CONFIG_PATH)!r}. '
            + "See '--print-config-schema' for reference."
        )

    try:
        await app.run()
    except Exception as error:
        logger.exception(error.__class__.__name__)
        show_exception_notification(error)
        parser.exit(2)


asyncio.run(main())
