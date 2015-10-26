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
CONFIG_DIR = click.get_app_dir(NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULT_FEED_INTERVAL_MINUTES = 60
DEFAULT_DIRECTORY = tempfile.gettempdir()
# click.launch uses os.system on Windows, which shows a cmd.exe window for a split second.
# hence os.startfile is preferred for that platform.
DEFAULT_COMMAND = os.startfile if os.name == 'nt' else click.launch
ON_FEED_EXCEPTION_ACTIONS = {'stop_this_feed', 'stop_all_feeds', 'continue'}
DEFAULT_ON_FEED_EXCEPTION_ACTION = 'continue'
PATH_ARGUMENT = '$PATH'
NUMBER_REGEX_GROUP = 'number'
TORRENT_MIMETYPE = 'application/x-bittorrent'

class ConfigError(Exception):
    pass

class Config(dict):
    def __init__(self):
        super().__init__()
        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__)

    def schema(self):
        json_bytes = pkg_resources.resource_string(__name__, 'config_schema.json')
        json_string = str(json_bytes, encoding='utf-8')
        return json.loads(json_string)

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
                    self.logger.debug("'Directory' {!r} exists but is not "
                                      'in fact a directory: {!r}', name, path)
                    raise NotADirectoryError(path)
                self.logger.debug('Directory {!r} exists: {!r}', name, path)
            else:
                os.makedirs(path)
                self.logger.info('Directory did not exist and was created: {!r}', path)
            directories[name] = path
            self.logger.debug('Directories key {!r} = {!r}', name, path)

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
        return self._get_from_other_dict(root_dict_key, instance_key,
                                         error_subscription_name, property_name)

    def _update_subscriptions(self):
        user_agent = self.json_dict.get('user_agent')

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

            subscription = Subscription(name, feed, regex, directory, command, user_agent)
            subscriptions[name] = feed.subscriptions[name] = subscription
            self.logger.debug('Subscriptions key {!r} = {!r}', name, subscription)

class Command:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return '{}(name={!r}, arguments={})'.format(type(self).__name__, self.name, self.arguments)

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
    def __init__(self, name, url, interval_minutes=DEFAULT_FEED_INTERVAL_MINUTES,
                 on_exception_action=DEFAULT_ON_FEED_EXCEPTION_ACTION):
        self.name = name
        self.url = url
        self.interval_minutes = interval_minutes
        self.on_exception_action = on_exception_action

        self.subscriptions = {}
        #TODO: separate file handlers for each feed's logger,
        #      since currently the log file's a huge clusterfuck
        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return ('{}(name={!r}, url={!r}, interval_minutes={}, subscriptions={})'
                .format(type(self).__name__, self.name, self.url,
                        self.interval_minutes, self.subscriptions.keys()))

    def fetch(self):
        rss = feedparser.parse(self.url)
        if rss.bozo:
            self.logger.critical('Error parsing {!r}', self.url)
            raise rss.bozo_exception
        self.logger.info('Parsed {!r}', self.url)
        return rss

    def matching_subscriptions(self):
        #TODO: Record which entries have been checked before
        #      to avoid needlessly checking them again every time.
        rss = self.fetch()
        for subscription in self.subscriptions.values():
            self.logger.debug('Checking entries against subscription {!r}', subscription.name)
            for index, entry in enumerate(rss.entries):
                match = subscription.regex.search(entry.title)
                if match:
                    number = pkg_resources.parse_version(match.group('number'))
                    if subscription.has_lower_number_than(number):
                        self.logger.info('MATCH: Entry {} titled {!r} has greater number than '
                                         'subscription {!r}; yielded: {} > {}', index, entry.title,
                                         subscription.name, number, subscription.number)
                        yield subscription, entry, number
                    else:
                        self.logger.debug('NO MATCH: Entry {} titled {!r} matches '
                                          'but number is smaller than or equal to that '
                                          'of subscription {!r}; skipped: {} <= {}',
                                          index, entry.title, subscription.name,
                                          number, subscription.number)
                else:
                    self.logger.debug('NO MATCH: Entry {} titled {!r} '
                                      'does not match subscription {!r}',
                                      index, entry.title, subscription.name)

class Subscription:
    forbidden_characters_regex = re.compile(r'[\\/:\*\?"<>\|]')

    def __init__(self, name, feed, regex, directory=DEFAULT_DIRECTORY,
                 command=DEFAULT_COMMAND, user_agent=None):
        self.name = name
        self.feed = feed
        self.regex = regex
        self.directory = directory
        self.command = command
        self.user_agent = user_agent

        self.number_file_path = os.path.join(CONFIG_DIR, self.name+'.number')
        self._number = None

        self.logger = logger.create_child(module_name=__name__, type_name=type(self).__name__,
                                          instance_name=self.name)

    def __repr__(self):
        return ('{}(name={!r}, feed={!r}, pattern={!r}, directory={!r}, command={!r}, number={})'
                .format(type(self).__name__, self.name, self.feed.name,
                        self.regex.pattern, self.directory, self.command, self.number))

    @property
    def number(self):
        if self._number is None:
            try:
                with open(self.number_file_path) as file:
                    line = file.readline()
                self._number = pkg_resources.parse_version(line)
                self.logger.info('Parsed {!r}; returning {}', self.number_file_path, self._number)
            except FileNotFoundError:
                self.logger.info('No number file found at {!r}; returning None',
                                 self.number_file_path)
        return self._number

    @number.setter
    def number(self, new_number):
        self._number = new_number
        with open(self.number_file_path, 'w') as file:
            file.write(str(new_number))
        self.logger.info('Number {} written to file {!r}',
                         new_number, self.number_file_path)

    def has_lower_number_than(self, other_number):
        return self.number is None or self.number < other_number

    def torrent_link_for(self, rss_entry):
        for link in rss_entry.links:
            if link.type == TORRENT_MIMETYPE:
                self.logger.debug('First link of entry {!r} with mimetype {!r}: {}',
                                  rss_entry.title, TORRENT_MIMETYPE, link.href)
                return link.href
        self.logger.info('Entry {!r} has no link with mimetype {!r}; returning first link: {}',
                         rss_entry.title, TORRENT_MIMETYPE, rss_entry.link)
        return rss_entry.link

    def torrent_path_for(self, title):
        fixed_title = re.sub(self.forbidden_characters_regex, '-', title)
        if fixed_title != title:
            self.logger.info('Title contained invalid characters: {!r} -> {!r}',
                             title, fixed_title)
        path = os.path.join(self.directory, fixed_title+'.torrent')
        self.logger.debug('Path for {!r}: {!r}', title, path)
        return path

    def download(self, rss_entry):
        link = self.torrent_link_for(rss_entry)
        headers = {} if self.user_agent is None else {'User-Agent': self.user_agent}
        self.logger.debug('Sending GET request to {!r} with headers {}', link, headers)
        response = requests.get(link, headers=headers)
        self.logger.debug("Response status code is {}, 'ok' is {}",
                          response.status_code, response.ok)
        response.raise_for_status()

        path = self.torrent_path_for(rss_entry.title)
        with open(path, 'wb') as file:
            file.write(response.content)
        self.logger.debug('Wrote response content to {!r}', path)
        return path
