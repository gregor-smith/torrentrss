import os
import re
import json
import platform
import tempfile
import subprocess

import click
import requests
import feedparser
import jsonschema
import pkg_resources

NAME = 'torrentrss'
VERSION = __version__ = '0.1'

SYSTEM = platform.system()

CONFIG_DIR = click.get_app_dir(NAME)
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULT_FEED_INTERVAL_MINUTES = 60
DEFAULT_DIRECTORY = tempfile.gettempdir()
DEFAULT_COMMAND = click.launch
PATH_ARGUMENT = '$PATH'
NUMBER_REGEX_GROUP = 'number'

class ConfigError(Exception):
    pass

class Config(dict):
    def __init__(self, path=CONFIG_PATH):
        super().__init__()
        self.path = path
        self.schema = self.get_schema()

    @staticmethod
    def get_schema():
        bytes_ = pkg_resources.resource_string(__name__, 'config_schema.json')
        string = str(bytes_, encoding='utf8')
        return json.loads(string)

    def load(self, path=None):
        with open(path or self.path) as file:
            self.json_dict = json.load(file)
        jsonschema.validate(self.json_dict, self.schema)

        self._update_simple_object('feeds', Feed)
        self._update_directories()
        self._update_simple_object('commands', Command)
        self._update_subscriptions()

    def _update_simple_object(self, key, new_type):
        self[key] = {dct['name']: new_type(**dct) for dct in self.json_dict[key]}

    def _update_directories(self):
        self['directories'] = directories = {}
        for directory in self.json_dict['directories']:
            name = directory['name']
            path = directory['path']
            if os.path.exists(path):
                if not os.path.isdir(path):
                    raise NotADirectoryError(path)
            else:
                os.makedirs(path)
            directories[name] = path

    def _get_from_other_dict(self, root_dict_key, instance_key,
                             error_subscription_name, error_property_name):
        try:
            return self[root_dict_key][instance_key]
        except KeyError as error:
            raise ConfigError('Subscription {!r} {} {!r} not defined'
                              .format(error_subscription_name,
                                      error_property_name, instance_key)) from error

    def _get_from_other_dict_with_default(self, subscription_dict, property_name,
                                          root_dict_key, default, error_subscription_name):
        try:
            instance_key = subscription_dict[property_name]
        except KeyError:
            return default
        else:
            return self._get_from_other_dict(root_dict_key, instance_key,
                                             error_subscription_name, property_name)

    def _update_subscriptions(self):
        self['subscriptions'] = subscriptions = {}
        for subscription in self.json_dict['subscriptions']:
            name = subscription['name']
            feed_name = subscription['feed']
            feed = self._get_from_other_dict(root_dict_key='feeds', instance_key=feed_name,
                                             error_subscription_name=name,
                                             error_property_name='feed')

            pattern_string = subscription['pattern']
            try:
                pattern = re.compile(pattern_string)
            except re.error as error:
                raise ConfigError('Subscription {!r} pattern {!r} not valid regular expression: {}'
                                  .format(name, pattern_string, ' - '.join(error.args))) from error
            if NUMBER_REGEX_GROUP not in pattern.groupindex:
                raise ConfigError('Subscription {!r} pattern {!r} has no {!r} group'
                                  .format(name, pattern_string, NUMBER_REGEX_GROUP))

            directory = self._get_from_other_dict_with_default(subscription,
                                                               property_name='directory',
                                                               root_dict_key='directories',
                                                               default=DEFAULT_DIRECTORY,
                                                               error_subscription_name=name)

            command = self._get_from_other_dict_with_default(subscription,
                                                             property_name='command',
                                                             root_dict_key='commands',
                                                             default=DEFAULT_COMMAND,
                                                             error_subscription_name=name)

            subscriptions[name] = feed.subscriptions[name] = Subscription(name, feed, pattern,
                                                                          directory, command)

class Command:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

    @staticmethod
    def identify_path_argument_index(args):
        for index, arg in args:
            if arg == PATH_ARGUMENT:
                return index
        raise ValueError('no path argument matching {!r} found in {}'.format(PATH_ARGUMENT, args))

    def __call__(self, path):
        args = self.args.copy()
        try:
            path_index = self.identify_path_argument_index(args)
            args[path_index] = path
        except ValueError:
            args.append(path)
        return subprocess.Popen(args)

class Feed:
    def __init__(self, name, url, interval_minutes=DEFAULT_FEED_INTERVAL_MINUTES):
        self.name = name
        self.url = url
        self.interval_minutes = interval_minutes
        self.subscriptions = {}

    def fetch(self):
        return feedparser.parse(self.url)

    def matching_subscriptions(self):
        rss = self.fetch()
        for subscription in self.subscriptions.values():
            for entry in rss.entries:
                match = subscription.pattern.search(entry.title)
                if match:
                    number = pkg_resources.parse_version(match.group('number'))
                    if number > subscription.number:
                        yield subscription, entry, number

class Subscription:
    forbidden_characters_pattern = re.compile(r'[\\/:\*\?"<>\|]')

    def __init__(self, name, feed, pattern, directory=DEFAULT_DIRECTORY, command=DEFAULT_COMMAND):
        self.name = name
        self.feed = feed
        self.pattern = pattern
        self.directory = directory
        self.command = command

        self.number_file_path = os.path.join(CONFIG_DIR, self.name+'.state')

    @property
    def number(self):
        try:
            return self._number
        except AttributeError:
            try:
                with open(self.number_file_path) as file:
                    self._number = pkg_resources.parse_version(file.readline())
            except FileNotFoundError:
                self._number = pkg_resources.parse_version('0')
            return self._number

    @number.setter
    def number(self, new_number):
        with open(self.number_file_path, 'w') as file:
            file.write(str(new_number))
        self._number = new_number

    def torrent_path_for(self, title):
        fixed_title = re.sub(self.forbidden_characters_pattern, '-', title)
        return os.path.join(self.directory, fixed_title+'.torrent')

    def download(self, rss_entry):
        response = requests.get(rss_entry.link)
        path = self.torrent_path_for(rss_entry.title)
        with open(path, 'wb') as file:
            file.write(response.content)
        return path
