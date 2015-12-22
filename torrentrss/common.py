import os
import re
import json
import random
import logging
import pathlib
import datetime
import tempfile
import contextlib
import subprocess

import click
import requests
import feedparser
import jsonschema
import pkg_resources

NAME = 'torrentrss'
CONFIG_DIR = pathlib.Path(click.get_app_dir(NAME))
CONFIG_PATH = CONFIG_DIR / 'config.json'

WINDOWS = os.name == 'nt'

LOG_MESSAGE_FORMAT = '[{asctime} {levelname}]:\n{message}'
LOG_PATH_FORMAT = 'logs/{0:%Y}/{0:%m}/{0:%Y-%m-%d_%H-%M-%S}.log'

USER_AGENTS = {
    'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.86 Safari/537.36',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:42.0) Gecko/20100101 Firefox/42.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.7 (KHTML, like Gecko) Version/9.0.1 Safari/601.2.7'
}
EXCEPTION_GUIS = {'Qt5', 'notify-send'}
DEFAULT_EXCEPTION_GUI = None
DEFAULT_FEED_ENABLED = DEFAULT_SUBSCRIPTION_ENABLED = True
DEFAULT_DIRECTORY = pathlib.Path(tempfile.gettempdir())
# click.launch uses os.system on Windows, which shows a cmd.exe window for a split second.
# hence os.startfile is preferred for that platform.
DEFAULT_COMMAND = os.startfile if WINDOWS else click.launch
COMMAND_PATH_ARGUMENT = '$PATH'
NUMBER_REGEX_GROUP = 'number'
TORRENT_MIMETYPE = 'application/x-bittorrent'

class ConfigError(Exception):
    pass

class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        with path.open() as file:
            self.json_dict = json.load(file)
        jsonschema.validate(self.json_dict, self.get_schema())

        self.user_agent = self.json_dict.get('user_agent', random.choice(USER_AGENTS))
        self.exception_gui = self.json_dict.get('exception_gui', DEFAULT_EXCEPTION_GUI)

        self.feeds = {}
        for feed in self.json_dict['feeds']:
            feed_name = feed['name']
            url = feed['url']
            feed_enabled = feed.get('enabled', DEFAULT_FEED_ENABLED)

            subscriptions = {}
            for sub in feed['subscriptions']:
                sub_name = sub['name']

                pattern = sub['pattern']
                try:
                    regex = re.compile(pattern)
                except re.error as error:
                    raise ConfigError('Feed {!r} subscription {!r} pattern {!r} not valid regex: {}'
                                      .format(feed_name, sub_name, pattern, ' - '.join(error.args))) from error
                if NUMBER_REGEX_GROUP not in regex.groupindex:
                    raise ConfigError('Feed {!r} subscription {!r} pattern {!r} has no {!r} group'
                                      .format(feed_name, sub_name, pattern, NUMBER_REGEX_GROUP))

                directory = pathlib.Path(sub['directory']) if 'directory' in sub else DEFAULT_DIRECTORY
                command = Command(sub['command']) if 'command' in sub else DEFAULT_COMMAND
                sub_enabled = sub.get('enabled', DEFAULT_SUBSCRIPTION_ENABLED)

                subsriptions[sub_name] = Subscription(sub_name, regex, directory,
                                                      command, sub_enabled)
            self.feeds[feed_name] = Feed(feed_name, url, feed_enabled, subscriptions)

    @staticmethod
    def get_schema():
        schema_bytes = pkg_resources.resource_string(__name__, 'config_schema.json')
        schema_string = str(schema_bytes, encoding='utf-8')
        return json.loads(schema_string)

    def enabled_feeds(self):
        for feed in self.feeds.values():
            if feed.enabled:
                yield feed

class Command:
    def __init__(self, arguments):
        self.arguments = arguments

    def __repr__(self):
        return '{}(arguments={})'.format(type(self).__name__, self.arguments)

    @staticmethod
    def identify_path_argument_index(arguments):
        for index, argument in enumerate(arguments):
            if argument == PATH_ARGUMENT:
                return index
        raise ValueError('no path argument matching {!r} found in {}'
                         .format(PATH_ARGUMENT, arguments))

    def __call__(self, path):
        arguments = self.arguments.copy()
        try:
            path_index = self.identify_path_argument_index(arguments)
            arguments[path_index] = path
        except ValueError:
            arguments.append(path)
        if WINDOWS:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None
        return subprocess.Popen(arguments, startupinfo=startupinfo)

class Feed:
    def __init__(self, name, url, enabled, subscriptions):
        self.name = name
        self.url = url
        self.interval_minutes = interval_minutes
        self.on_exception_action = on_exception_action
        self.on_exception_gui = on_exception_gui
        self.enabled = enabled

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

    def enabled_subscriptions(self):
        for subscription in self.subscriptions.values():
            if subscription.enabled:
                yield subscription

    def matching_subscriptions(self):
        #TODO: Record which entries have been checked before
        #      to avoid needlessly checking them again every time.
        rss = self.fetch()
        for subscription in self.enabled_subscriptions():
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

    def has_enabled_subscription(self):
        try:
            next(self.enabled_subscriptions())
            return True
        except StopIteration:
            return False

class Subscription:
    forbidden_characters_regex = re.compile(r'[\\/:\*\?"<>\| ]')

    def __init__(self, name, regex, directory, command, enabled):
        self.name = name
        self.regex = regex
        self.directory = directory
        self.command = command
        self.enabled = enabled

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
        fixed_title = re.sub(self.forbidden_characters_regex, '_', title)
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

def configure_logging(file_logging_level, console_logging_level):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_logging_level)

    file_path = CONFIG_DIR / LOG_PATH_FORMAT.format(datetime.datetime.now())
    file_handler = logging.FileHandler(str(file_path))
    file_handler.setLevel(file_logging_level)

    logging.basicConfig(format=LOG_MESSAGE_FORMAT, handlers=[file_handler, console_handler])

@contextlib.contextmanager
def log_exception(*message, reraise=True):
    try:
        yield
    except Exception as exception:
        logging.exception(*message) if message else logging.exception('%s', type(exception))
        if reraise:
            raise
