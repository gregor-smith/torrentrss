import click

from . import common

@click.command()
@click.option('--config-path', type=click.Path(exists=True, dir_okay=False))
@click.version_option(common.VERSION)
def main(config_path):
    pass
