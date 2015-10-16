import click

from . import common, loop

@click.command()
@click.option('--config-path', type=click.Path(exists=True, dir_okay=False))
@click.version_option(common.VERSION)
def main(config_path):
    try:
        config = common.Config(config_path or common.CONFIG_PATH)
    except FileNotFoundError:
        raise click.Abort('No config file found at {!r}. See the example in the package.'
                          .format(common.CONFIG_PATH))
    config.load()

    loop.run(config)
