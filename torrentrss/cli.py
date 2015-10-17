import logging

import click

from . import common, loop, logger

logging_level_choice_type = click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

def logging_level_from_string(context, parameter, value):
    return getattr(logging, value)

@click.command()
@click.option('--config-path', type=click.Path(exists=True, dir_okay=False))
@click.option('--file-logging-level', type=logging_level_choice_type, default='DEBUG',
              callback=logging_level_from_string)
@click.option('--console-logging-level', type=logging_level_choice_type, default='WARNING',
              callback=logging_level_from_string)
@click.version_option(common.VERSION)
def main(config_path, file_logging_level, console_logging_level):
    log = logger.create(common.CONFIG_DIR, file_logging_level, console_logging_level)

    config = common.Config()
    with log.catch_exception():
        try:
            config.load(config_path or common.CONFIG_PATH)
        except FileNotFoundError as error:
            raise click.Abort('No config file found at {!r}. See the example in the package.'
                              .format(common.CONFIG_PATH)) from error

    log.info('starting loop')
    loop.run(config)
