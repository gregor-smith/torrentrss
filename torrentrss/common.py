import os
import json
import platform
import tempfile
import subprocess
import collections

import click
import jsonschema

NAME = 'torrentrss'
VERSION = __version__ = '0.1'

SYSTEM = platform.system()

CONFIG_DIR = click.get_app_dir(NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULT_FEED_INTERVAL_MINUTES = 60
DEFAULT_DIRECTORY = tempfile.gettempdir()
DEFAULT_COMMAND = click.launch
PATH_ARGUMENT = '$PATH'
NUMBER_REGEX_GROUP = 'number'

class Config(dict):
    def __init__(self, path=CONFIG_PATH):
        super().__init__()
        self.path = path

    def load(self, path=None):
        with open(path or self.path) as file:
            self.json_dict = json.load(file)
        #jsonschema.validate(self.json_dict)
        self.update(self.json_dict)

        #for feed in self['feeds']:


class Command:
    def __init__(self, name, args):
        self.name = name
        self.args = args

    @staticmethod
    def identify_path_argument_index(args):
        for index, arg in args:
            if arg == PATH_ARGUMENT:
                return index
        raise ValueError('no path argument matching {!r} found in {}'.format(PATH_ARGUMENT, args))

    def __call__(self, path):
        args = self.args.copy()
        try:
            path_index = identify_path_argument_index(args)
            args[path_index] = path
        except ValueError:
            args.append(path)
        return subprocess.Popen(args)

class Feed:
    def __init__(self, name, url, interval):
        self.name = name
        self.url = url
        self.interval = interval
        self.subscriptions = {}

Subscription = collections.namedtuple('Subscription', ['name', 'feed', 'pattern',
                                                       'directory', 'command'])

