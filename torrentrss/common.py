import os
import re
import json
import datetime
import tempfile
import subprocess

import click
import requests
import feedparser
import jsonschema
import pkg_resources

from . import logger

NAME = logger.ROOT_NAME
VERSION = __version__ = '0.1'

CONFIG_DIR = click.get_app_dir(NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULT_FEED_INTERVAL_MINUTES = 60
DEFAULT_DIRECTORY = tempfile.gettempdir()
DEFAULT_COMMAND = click.launch
PATH_ARGUMENT = '$PATH'
NUMBER_REGEX_GROUP = 'number'

class ConfigError(Exception):
    pass

class Config(dict):
    def __init__(self):
        super().__init__()
        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__)

    def schema(self):
        bytes_ = pkg_resources.resource_string(__name__, 'config_schema.json')
        string = str(bytes_, encoding='utf-8')
        return json.loads(string)

    def load(self, path=CONFIG_PATH):
        self.logger.debug('Config path: {!r}', path)

        with open(path) as file:
            self.json_dict = json.load(file)
        jsonschema.validate(self.json_dict, self.schema())

        self._update_simple_object('feeds', Feed)
        self._update_directories()
        self._update_simple_object('commands', Command)
        self._update_subscriptions()

        self.path = path

    def _update_simple_object(self, key, new_type):
        self[key] = {dct['name']: new_type(**dct) for dct in self.json_dict[key]}

    def _update_directories(self):
        self['directories'] = directories = {}
        for directory in self.json_dict['directories']:
            name = directory['name']
            path = directory['path']
            if os.path.exists(path):
                if not os.path.isdir(path):
                    self.logger.debug("'Directory' {!r} exists but is not in fact a directory: {}", name, path)
                    raise NotADirectoryError(path)
                self.logger.debug('Directory {!r} exists: {}', name, path)
            else:
                os.makedirs(path)
                self.logger.info('Directory did not exist and was created: {}', path)
            directories[name] = path
            self.logger.debug('Directories key {!r} = {}', name, path)

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
        #TODO: more logging here
        self['subscriptions'] = subscriptions = {}
        for subscription in self.json_dict['subscriptions']:
            name = subscription['name']
            feed_name = subscription['feed']
            feed = self._get_from_other_dict(root_dict_key='feeds', instance_key=feed_name,
                                             error_subscription_name=name,
                                             error_property_name='feed')

            pattern = subscription['pattern']
            try:
                regex = re.compile(pattern)
            except re.error as error:
                raise ConfigError('Subscription {!r} pattern {!r} not valid regular expression: {}'
                                  .format(name, pattern, ' - '.join(error.args))) from error
            if NUMBER_REGEX_GROUP not in regex.groupindex:
                raise ConfigError('Subscription {!r} pattern {!r} has no {!r} group'
                                  .format(name, pattern, NUMBER_REGEX_GROUP))

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

            subscription = Subscription(name, feed, regex, directory, command)
            subscriptions[name] = feed.subscriptions[name] = subscription
            self.logger.debug('Subscriptions key {!r} = {!r}', name, subscription)

class Command:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return '{}(name={!r}, arguments={})'.format(type(self.__name__),
                                                      self.name, self.arguments)

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

        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return ('{}(name={!r}, url={!r}, interval_minutes={}, subscriptions={})'
                .format(type(self.__name__), self.name, self.url,
                        self.interval_minutes, self.subscriptions.keys()))

    def fetch(self):
        return feedparser.parse(self.url)

    def matching_subscriptions(self):
        rss = self.fetch()
        for subscription in self.subscriptions.values():
            for entry in rss.entries:
                match = subscription.regex.search(entry.title)
                if match:
                    number = pkg_resources.parse_version(match.group('number'))
                    if number > subscription.number:
                        yield subscription, entry, number

class Subscription:
    forbidden_characters_pattern = re.compile(r'[\\/:\*\?"<>\|]')

    def __init__(self, name, feed, regex, directory=DEFAULT_DIRECTORY, command=DEFAULT_COMMAND):
        self.name = name
        self.feed = feed
        self.regex = regex
        self.directory = directory
        self.command = command

        self.number_file_path = os.path.join(CONFIG_DIR, self.name+'.number')

        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return ('{}(name={!r}, feed={!r}, pattern={}, directory={}, command={}, number={})'
                .format(type(self.__name__), self.feed.name, self.regex.pattern,
                        self.directory, self.command.name, self.number))

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
