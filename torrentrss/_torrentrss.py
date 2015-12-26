import os
import re
import json
import random
import shutil
import logging
import pathlib
import datetime
import tempfile
import traceback
import contextlib
import subprocess

import click
import requests
import feedparser
import jsonschema
import pkg_resources

NAME = 'torrentrss'
VERSION = __version__ = '0.2'

WINDOWS = os.name == 'nt'

CONFIG_DIR = pathlib.Path(click.get_app_dir(NAME))
CONFIG_PATH = CONFIG_DIR / 'config.json'
LOG_DIR = CONFIG_DIR / 'logs'

LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'
DEFAULT_LOG_PATH_FORMAT = '%Y/%m/%Y-%m-%d.log'

# TODO: better means of fetching common user agents
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.86 Safari/537.36',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:42.0) Gecko/20100101 Firefox/42.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.7 (KHTML, like Gecko) Version/9.0.1 Safari/601.2.7'
]
EXCEPTION_GUIS = ['Qt5', 'notify-send']
DEFAULT_EXCEPTION_GUI = None
HAS_NOTIFY_SEND = shutil.which('notify-send') is not None
DEFAULT_FEED_ENABLED = DEFAULT_SUBSCRIPTION_ENABLED = DEFAULT_MAGNET_ENABLED = True
TEMP_DIRECTORY = pathlib.Path(tempfile.gettempdir())
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

        self.exception_gui = self.json_dict.get('exception_gui', DEFAULT_EXCEPTION_GUI)
        if self.exception_gui == 'Qt5':
            # PyQt5 is only imported on demand as it's fairly hefty. Doing as below avoids
            # unnecessarily long startup times in cases where it's installed but isn't to be used.
            try:
                import PyQt5
            except ImportError as error:
                raise ConfigError("'exception_gui' is 'Qt5' but PyQt5 failed to import: {}"
                                  .format(' - '.join(error.args))) from error
        elif self.exception_gui == 'notify-send' and not HAS_NOTIFY_SEND:
            raise ConfigError("'exception_gui' is 'notify-send' but notify-send"
                              'could not be found on the PATH')
        elif self.exception_gui is not None:
            raise ConfigError("'exception_gui' {!r} unknown. Must be one of {}"
                              .format(EXCEPTION_GUIS))

        self.feeds = {}
        for feed in self.json_dict['feeds']:
            feed_name = feed['name']
            url = feed['url']
            user_agent = (feed['user_agent'] if 'user_agent' in feed
                          else random.choice(USER_AGENTS))
            feed_enabled = feed.get('enabled', DEFAULT_FEED_ENABLED)
            magnet_enabled = feed.get('magnet_enabled', DEFAULT_MAGNET_ENABLED)

            subscriptions = {}
            for sub in feed['subscriptions']:
                sub_name = sub['name']

                pattern = sub['pattern']
                try:
                    regex = re.compile(pattern)
                except re.error as error:
                    raise ConfigError("Feed {!r} subscription {!r} pattern '{}' not valid regex: {}"
                                      .format(feed_name, sub_name, pattern, ' - '.join(error.args))) from error
                if NUMBER_REGEX_GROUP not in regex.groupindex:
                    raise ConfigError("Feed {!r} subscription {!r} pattern '{}' has no {!r} group"
                                      .format(feed_name, sub_name, pattern, NUMBER_REGEX_GROUP))

                directory = (pathlib.Path(sub['directory']) if 'directory' in sub
                             else TEMP_DIRECTORY)
                command = (Command(sub_name, sub['command']) if 'command' in sub
                           else StartFileCommand(sub_name))
                sub_enabled = sub.get('enabled', DEFAULT_SUBSCRIPTION_ENABLED)

                subscriptions[sub_name] = Subscription(sub_name, regex, directory,
                                                       command, sub_enabled)
            self.feeds[feed_name] = Feed(feed_name, url, user_agent, feed_enabled,
                                         magnet_enabled, subscriptions)

    def __repr__(self):
        return '{}(path={!r})'.format(type(self).__name__, self.path)

    @staticmethod
    def get_schema():
        schema_bytes = pkg_resources.resource_string(__name__, 'config_schema.json')
        schema_string = str(schema_bytes, encoding='utf-8')
        return json.loads(schema_string)

    @contextlib.contextmanager
    def errors_shown_as_gui(self):
        try:
            yield
        except Exception as error:
            if self.exception_gui is not None:
                text = '{} encountered {!r} exception.'.format(NAME, type(error))
                error_traceback = traceback.format_exc()
                if self.exception_gui == 'notify-send':
                    self.show_error_notification(text, error_traceback)
                elif self.exception_gui == 'Qt5':
                    self.show_error_pyqt5_messagebox(text, error_traceback)
            raise

    @staticmethod
    def show_error_pyqt5_messagebox(text, error_traceback):
        import PyQt5.QtWidgets

        qapplication = PyQt5.QtWidgets.QApplication([])

        messagebox = PyQt5.QtWidgets.QMessageBox()
        messagebox.setWindowTitle(NAME)
        messagebox.setText(text)
        messagebox.setDetailedText(error_traceback)

        ok_button = messagebox.addButton(messagebox.Ok)
        open_button = messagebox.addButton('Open Log Dir', messagebox.ActionRole)
        messagebox.setDefaultButton(ok_button)

        messagebox.exec_()
        if messagebox.clickedButton() == open_button:
            startfile(LOG_DIR)

    @staticmethod
    def show_error_notification(text):
        message = '{} Click to open log directory:\n{}'.format(text, LOG_DIR.as_uri())
        subprocess.Popen(['notify-send', '--app-name', NAME, NAME, message])

    def check_feeds(self):
        for feed in self.feeds.values():
            if feed.enabled and feed.has_any_enabled_subscriptions():
                # List is called here as otherwise subscription.number would be updated during the
                # loop before being checked by the next iteration of feed.matching_subscriptions,
                # so if a subscription's number was originally 2 and there were entries with 4 and 3,
                # 4 would become the subscription's number, and because 4 > 3, 3 would be skipped.
                # Calling list first checks all entries against the subscription's original number,
                # avoiding this problem. The alternatives were to update numbers in another loop
                # afterwards, or to call reversed first on rss.entries in feed.matching_subscriptions.
                # The latter seems like an ok workaround at first, since it would yield 3 before 4,
                # but if 4 were added to the rss before 3 for some reason, it would still break.
                for subscription, entry, number in list(feed.matching_subscriptions()):
                    path_or_magnet = feed.download_entry(entry, subscription.directory)
                    subscription.command(path_or_magnet)
                    if subscription.has_lower_number_than(number):
                        subscription.number = number

class Command:
    path_replacement_regex = re.compile(re.escape(COMMAND_PATH_ARGUMENT))

    def __init__(self, subscription_name, arguments):
        self.subscription_name = subscription_name
        self.arguments = arguments

    def __repr__(self):
        return ('{}(subscription_name={!r}, arguments={})'
                .format(type(self).__name__, self.subscription_name, self.arguments))

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
                     self.subscription_name, arguments)
        return subprocess.Popen(arguments, startupinfo=startupinfo)

class StartFileCommand(Command):
    def __init__(self, subscription_name):
        self.subscription_name = subscription_name

    def __repr__(self):
        return '{}(subscription_name={!r}'.format(self.subscription_name)

    def __call__(self, path):
        logging.debug("Subscription %r: launching '%s' with default program",
                      subscription_name, path)
        startfile(path)

class Feed:
    def __init__(self, name, url, user_agent, enabled, magnet_enabled, subscriptions):
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.enabled = enabled
        self.magnet_enabled = magnet_enabled
        self.subscriptions = subscriptions

    def __repr__(self):
        return ('{}(name={!r}, url={!r}, interval_minutes={}, subscriptions={})'
                .format(type(self).__name__, self.name, self.url,
                        self.interval_minutes, self.subscriptions.keys()))

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
                    number = pkg_resources.parse_version(match.group('number'))
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
    def torrent_link_for_entry(rss_entry):
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
            logging.debug('Entry %r: has magnet link %r',
                          rss_entry.title, rss_entry.torrent_magneturi)
            return rss_entry.torrent_magneturi

        link = self.torrent_link_for_entry(rss_entry)
        headers = {} if self.user_agent is None else {'User-Agent': self.user_agent}
        logging.debug('Feed %r: sending GET request to %r with headers %s',
                      self.name, link, headers)
        response = requests.get(link, headers=headers)
        logging.debug("Feed %r: response status code is %s, 'ok' is %s",
                      self.name, response.status_code, response.ok)
        response.raise_for_status()

        directory.mkdir(parents=True, exist_ok=True)
        path = windows_safe_path(directory / (rss_entry.title+'.torrent'))
        path.write_bytes(response.content)
        logging.debug("Feed %r: wrote response bytes to file '%s'", self.name, path)
        return str(path)

class Subscription:
    def __init__(self, name, regex, directory, command, enabled):
        self.name = name
        self.regex = regex
        self.directory = directory
        self.command = command
        self.enabled = enabled

        self.number_file_path = windows_safe_path(CONFIG_DIR / 'episode_numbers' / self.name)
        self._number = None

    def __repr__(self):
        return ('{}(name={!r}, pattern={!r}, directory={!r}, command={!r}, number={})'
                .format(type(self).__name__, self.name, self.regex.pattern,
                        self.directory, self.command, self.number))

    @property
    def number(self):
        if self._number is None:
            try:
                with self.number_file_path.open() as file:
                    line = file.readline()
                self._number = pkg_resources.parse_version(line)
                logging.info("Subscription %r: got number %s from file '%s'",
                             self.name, self._number, self.number_file_path)
            except FileNotFoundError:
                logging.info("Subscription %r: no number file found at '%s', returning None",
                             self.name, self.number_file_path)
        return self._number

    @number.setter
    def number(self, new_number):
        self._number = new_number
        self.number_file_path.write_text(str(new_number))
        logging.info("Subscription %r: wrote number %s to file '%s'",
                     self.name, new_number, self.number_file_path)

    def has_lower_number_than(self, other_number):
        return self.number is None or self.number < other_number

# click.launch uses os.system on Windows, which shows a cmd.exe window for a split second.
# hence os.startfile is preferred for that platform.
startfile = os.startfile if WINDOWS else click.launch

windows_forbidden_characters_regex = re.compile(r'[\\/:\*\?"<>\| ]')
def windows_safe_path(path):
    if WINDOWS:
        new_name = windows_forbidden_characters_regex.sub('_', path.name)
        return path.with_name(new_name)
    return path

def configure_logging(log_path_format, file_logging_level, console_logging_level):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_logging_level)

    path = LOG_DIR / datetime.datetime.now().strftime(log_path_format)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(str(path))
    file_handler.setLevel(file_logging_level)

    # silence requests' logging in all but the worst cases
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    logging.basicConfig(format=LOG_MESSAGE_FORMAT, level=file_logging_level,
                        handlers=[file_handler, console_handler])

@contextlib.contextmanager
def exception_logging():
    try:
        yield
    except Exception as error:
        logging.exception(type(error))
        raise

def logging_level_from_string(context, parameter, value):
    return getattr(logging, value)

logging_level_choice = click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

@click.command()
@click.option('--log-path-format', default=DEFAULT_LOG_PATH_FORMAT, show_default=True)
@click.option('--file-logging-level', type=logging_level_choice, show_default=True,
              default='DEBUG', callback=logging_level_from_string)
@click.option('--console-logging-level', type=logging_level_choice, show_default=True,
              default='INFO', callback=logging_level_from_string)
@click.version_option(VERSION)
def main(log_path_format, file_logging_level, console_logging_level):
    configure_logging(log_path_format, file_logging_level, console_logging_level)

    with exception_logging():
        try:
            config = Config()
        except FileNotFoundError as error:
            raise click.Abort("No config file found at '{}'. See the schema in the package."
                              .format(CONFIG_PATH)) from error
        with config.errors_shown_as_gui():
            config.check_feeds()
