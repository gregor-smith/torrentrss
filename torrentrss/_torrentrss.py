import os
import re
import sys
import json
import shutil
import hashlib
import logging
import pathlib
import datetime
import tempfile
import contextlib
import subprocess
import collections

import click
import easygui
import requests
import feedparser
import jsonschema
import pkg_resources

NAME = 'torrentrss'
VERSION = '0.4.1'

WINDOWS = os.name == 'nt'

CONFIG_DIR = pathlib.Path(click.get_app_dir(NAME))
CONFIG_PATH = CONFIG_DIR / 'config.json'
CONFIG_SCHEMA_FILENAME = 'config_schema.json'

LOG_DIR = CONFIG_DIR / 'logs'
LOG_PATH_FORMAT = '%Y/%m/%Y-%m-%d.log'
LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'
DEFAULT_LOG_FILE_LIMIT = 1

TEMP_DIRECTORY = pathlib.Path(tempfile.gettempdir())
COMMAND_PATH_ARGUMENT = '$PATH_OR_URL'
TORRENT_MIMETYPE = 'application/x-bittorrent'

# click.launch uses os.system on Windows, which shows a cmd.exe window for a split second.
# hence os.startfile is preferred for that platform.
startfile = os.startfile if WINDOWS else click.launch

windows_forbidden_characters_regex = re.compile(r'[\\/:\*\?"<>\|]')
def windows_safe_path(path):
    if WINDOWS:
        new_name = windows_forbidden_characters_regex.sub('_', path.name)
        return path.with_name(new_name)
    return path

def show_notify_send_exception_gui():
    text = ('A {} exception occured. <a href="{}">Click to open the log directory.</a>'
            .format(sys.last_type.__name__, LOG_DIR.as_uri()))
    return subprocess.Popen(['notify-send', '--app-name', NAME, NAME, text])

def show_easygui_exception_gui():
    text = 'A {} exception occured.'.format(sys.last_type.__name__)
    return easygui.exceptionbox(msg=text, title=NAME)

class ConfigError(Exception):
    pass

class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        with path.open(encoding='utf-8') as file:
            self.json_dict = json.load(file, object_pairs_hook=collections.OrderedDict)
        jsonschema.validate(self.json_dict, self.get_schema_dict())

        self.exception_gui = self.json_dict.get('exception_gui')
        if self.exception_gui == 'notify-send' and shutil.which('notify-send') is None:
            raise ConfigError("'exception_gui' is 'notify-send' but it "
                              'could not be found on the PATH')
        elif self.exception_gui != 'easygui' and self.exception_gui is not None:
            raise ConfigError("'exception_gui' {!r} unknown. Must be 'notify-send' or 'easygui'"
                              .format(self.exception_gui))

        self.remove_old_log_files_enabled = self.json_dict.get('remove_old_log_files_enabled',
                                                               True)
        self.log_file_limit = self.json_dict.get('log_file_limit', DEFAULT_LOG_FILE_LIMIT)
        self.remove_old_number_files_enabled = self.json_dict.get('remove_old_number_files_enabled',
                                                                  True)

        with self.exceptions_shown_as_gui():
            self.feeds = {name: Feed(name, **feed_dict) for name, feed_dict in
                          self.json_dict['feeds'].items()}

    def __repr__(self):
        return '{}(path={!r})'.format(type(self).__name__, self.path)

    @staticmethod
    def get_schema():
        schema = pkg_resources.resource_string(__name__, CONFIG_SCHEMA_FILENAME)
        return str(schema, encoding='utf-8')

    @classmethod
    def get_schema_dict(cls):
        return json.loads(cls.get_schema())

    @contextlib.contextmanager
    def exceptions_shown_as_gui(self):
        try:
            yield
        except Exception:
            if self.exception_gui == 'notify-send':
                show_notify_send_exception_gui()
            elif self.exception_gui == 'easygui':
                show_easygui_exception_gui()
            raise

    def check_feeds(self):
        with self.exceptions_shown_as_gui():
            for feed in self.feeds.values():
                if feed.enabled and feed.has_any_enabled_subscriptions():
                    # List is called here as otherwise subscription.number would be updated during the
                    # loop before being checked by the next iteration of feed.matching_subscriptions,
                    # so if a subscription's number was originally 2 and there were entries with 4 and 3,
                    # 4 would become the subscription's number, and because 4 > 3, 3 would be skipped.
                    # Calling list first checks all entries against the subscription's original number,
                    # avoiding this problem.
                    for subscription, entry, number in list(feed.matching_subscriptions()):
                        path_or_magnet = feed.download_entry(entry, subscription.directory)
                        subscription.command(path_or_magnet)
                        if subscription.has_lower_number_than(number):
                            subscription.number = number

    def remove_old_log_files(self):
        count = 0
        removed_directories = set()
        for directory, subdirectories, files in reversed(list(os.walk(str(LOG_DIR)))):
            directory = pathlib.Path(directory)
            files_copy = files.copy()
            subdirectories_copy = subdirectories.copy()
            for filename in reversed(files):
                file = directory / filename
                if count >= self.log_file_limit:
                    logging.debug("Removing old log file '%s'", file)
                    os.remove(str(file))
                    files_copy.remove(filename)
                else:
                    count += 1
                    logging.debug("Skipping log file %s/%s '%s'", count, self.log_file_limit, file)
            for subdirectory_name in subdirectories:
                subdirectory = directory / subdirectory_name
                if subdirectory in removed_directories:
                    subdirectories_copy.remove(subdirectory_name)
            if not subdirectories_copy and not files_copy:
                logging.debug("Removing log directory '%s' as it has no "
                              'remaining subdirectories or files', directory)
                directory.rmdir()
                removed_directories.add(directory)

    def save_with_new_numbers(self):
        logging.info("Writing new number files to '%s'", self.path)
        for feed_name, feed in self.feeds.items():
            feed_subscriptions_dict = self.json_dict['feeds'][feed_name]['subscriptions']
            for subscription_name, subscription in feed.subscriptions.items():
                feed_subscriptions_dict[subscription_name]['number'] = str(subscription.number)
        with self.path.open('w', encoding='utf-8') as file:
            json.dump(self.json_dict, file, indent='\t')

class Command:
    path_replacement_regex = re.compile(re.escape(COMMAND_PATH_ARGUMENT))

    def __init__(self, subscription, arguments):
        self.subscription = subscription
        self.arguments = arguments

    def __repr__(self):
        return ('{}(subscription={!r}, arguments={})'
                .format(type(self).__name__, self.subscription.name, self.arguments))

    def __call__(self, path):
        if WINDOWS:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None
        # The re.sub call's repl parameter here is a function which at first looks like it could
        # just be a string, but it actually needs to be a function or else escapes in the string
        # would be processed, leading to problems when dealing with file paths, for example.
        # See: https://docs.python.org/3.5/library/re.html#re.sub
        #      https://stackoverflow.com/a/16291763/3289208
        arguments = [self.path_replacement_regex.sub(lambda match: str(path), argument)
                     for argument in self.arguments]
        logging.info('Subscription %r: running command subprocess with arguments %s',
                     self.subscription.name, arguments)
        return subprocess.Popen(arguments, startupinfo=startupinfo)

class StartFileCommand(Command):
    def __init__(self, subscription):
        self.subscription = subscription

    def __repr__(self):
        return '{}(subscription={!r}'.format(type(self).__name__, self.subscription.name)

    def __call__(self, path):
        logging.debug("Subscription %r: launching '%s' with default program",
                      self.subscription.name, path)
        startfile(path)

class Feed:
    def __init__(self, name, url, subscriptions, user_agent=None, enabled=True,
                 magnet_enabled=True, torrent_url_enabled=True, hide_torrent_filename_enabled=True):
        self.name = name
        self.url = url
        self.subscriptions = {name: Subscription(self, name, **subscription_dict)
                              for name, subscription_dict in subscriptions.items()}
        self.user_agent = user_agent
        self.enabled = enabled
        self.magnet_enabled = magnet_enabled
        self.torrent_url_enabled = torrent_url_enabled
        self.hide_torrent_filename_enabled = hide_torrent_filename_enabled

    def __repr__(self):
        return ('{}(name={!r}, url={!r}, subscriptions={})'
                .format(type(self).__name__, self.name, self.url, self.subscriptions.keys()))

    def fetch(self):
        rss = feedparser.parse(self.url)
        if rss.bozo:
            logging.critical('Feed %r: error parsing url %r', self.name, self.url)
            raise rss.bozo_exception
        logging.info('Feed %r: downloaded url %r', self.name, self.url)
        return rss

    def enabled_subscriptions(self):
        for subscription in self.subscriptions.values():
            if subscription.enabled:
                yield subscription

    def matching_subscriptions(self):
        rss = self.fetch()
        for subscription in self.enabled_subscriptions():
            logging.debug("Subscription %r: checking entries against pattern: %s",
                          subscription.name, subscription.regex.pattern)
            for index, entry in enumerate(rss.entries):
                match = subscription.regex.search(entry.title)
                if match:
                    number = pkg_resources.parse_version(match.group(1))
                    if subscription.has_lower_number_than(number):
                        logging.info('MATCH: entry %s %r has greater number than subscription %r: '
                                     '%s > %s', index, entry.title, subscription.name,
                                     number, subscription.number)
                        yield subscription, entry, number
                    else:
                        logging.debug('NO MATCH: entry %s %r matches but number less than or '
                                      'equal to subscription %r: %s <= %s', index, entry.title,
                                      subscription.name, number, subscription.number)
                else:
                    logging.debug('NO MATCH: entry %s %r against subscription %r',
                                  index, entry.title, subscription.name)

    def has_any_enabled_subscriptions(self):
        try:
            next(self.enabled_subscriptions())
            return True
        except StopIteration:
            return False

    @staticmethod
    def torrent_url_for_entry(rss_entry):
        for link in rss_entry.links:
            if link.type == TORRENT_MIMETYPE:
                logging.debug('Entry %r: first link with mimetype %r is %r',
                              rss_entry.title, TORRENT_MIMETYPE, link.href)
                return link.href
        logging.info('Entry %r: no link with mimetype %r, returning first link %r',
                     rss_entry.title, TORRENT_MIMETYPE, rss_entry.link)
        return rss_entry.link

    def download_entry(self, rss_entry, directory):
        if self.magnet_enabled and hasattr(rss_entry, 'torrent_magneturi'):
            logging.debug('Entry %r: has magnet url %r',
                          rss_entry.title, rss_entry.torrent_magneturi)
            return rss_entry.torrent_magneturi

        url = self.torrent_url_for_entry(rss_entry)
        if self.torrent_url_enabled:
            logging.debug('Feed %r: returning torrent url %r', self.name, url)
            return url
        headers = {} if self.user_agent is None else {'User-Agent': self.user_agent}
        logging.debug('Feed %r: sending GET request to %r with headers %s',
                      self.name, url, headers)
        response = requests.get(url, headers=headers)
        logging.debug("Feed %r: response status code is %s, 'ok' is %s",
                      self.name, response.status_code, response.ok)
        response.raise_for_status()

        directory.mkdir(parents=True, exist_ok=True)
        title = (hashlib.sha1(response.content).hexdigest()
                 if self.hide_torrent_filename_enabled else rss_entry.title)
        path = windows_safe_path(directory / title).with_suffix('.torrent')
        path.write_bytes(response.content)
        logging.debug("Feed %r: wrote response bytes to file '%s'", self.name, path)
        return str(path)

class Subscription:
    def __init__(self, feed, name, pattern, number=None, directory=None, command=None, enabled=True):
        self.feed = feed
        self.name = name
        try:
            self.regex = re.compile(pattern)
        except re.error as error:
            raise ConfigError("Feed {!r} subscription {!r} pattern '{}' not valid regex: {}"
                              .format(feed.name, self.name, pattern, ' - '.join(error.args))) from error
        if not self.regex.groups:
            raise ConfigError("Feed {!r} subscription {!r} pattern '{}' has no group "
                              'for the episode number'.format(feed.name, self.name, pattern))
        self.number = None if number is None else pkg_resources.parse_version(number)
        self.directory = TEMP_DIRECTORY if directory is None else pathlib.Path(directory)
        self.command = StartFileCommand(self) if command is None else Command(self, command)
        self.enabled = enabled

    def __repr__(self):
        return ('{}(name={!r}, pattern={!r}, directory={!r}, command={!r}, enabled={}, number={})'
                .format(type(self).__name__, self.name, self.regex.pattern,
                        self.directory, self.command, self.enabled, self.number))

    def has_lower_number_than(self, other_number):
        return self.number is None or self.number < other_number

def configure_logging(path_format=LOG_PATH_FORMAT, message_format=LOG_MESSAGE_FORMAT,
                      file_level=None, console_level=None):
    handlers = []
    level = 0

    if file_level is not None:
        path = LOG_DIR / datetime.datetime.now().strftime(path_format)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(path), encoding='utf-8')
        file_handler.setLevel(file_level)

        handlers.append(file_handler)
        level = file_level

    if console_level is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)

        handlers.append(console_handler)
        if console_level < level:
            level = console_level

    if handlers:
        logging.basicConfig(format=message_format, handlers=handlers, level=level)

    # silence requests' logging in all but the worst cases
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

logging_level_choice = click.Choice(['DISABLE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

def logging_level_from_string(context, parameter, level):
    return getattr(logging, level, None)

def print_schema(context, parameter, value):
    if value:
        print(Config.get_schema())
        context.exit()

@click.command()
@click.option('--log-path-format', default=LOG_PATH_FORMAT, show_default=True)
@click.option('--file-logging-level', default='DEBUG', show_default=True,
              type=logging_level_choice, callback=logging_level_from_string)
@click.option('--console-logging-level', default='INFO', show_default=True,
              type=logging_level_choice, callback=logging_level_from_string)
@click.option('--print-schema', is_flag=True, is_eager=True,
              expose_value=False, callback=print_schema)
@click.version_option(VERSION)
def main(log_path_format, file_logging_level, console_logging_level):
    configure_logging(log_path_format, file_level=file_logging_level,
                      console_level=console_logging_level)

    try:
        try:
            config = Config()
        except FileNotFoundError as error:
            raise click.Abort("No config file found at '{}'. Try '--print-schema'."
                              .format(CONFIG_PATH)) from error
        config.check_feeds()
        config.save_with_new_numbers()
        if config.remove_old_log_files_enabled:
            config.remove_old_log_files()
    except Exception as error:
        logging.exception(type(error))
        raise

    logging.shutdown()
