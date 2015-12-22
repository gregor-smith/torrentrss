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
CONFIG_DIR = pathlib.Path(click.get_app_dir(NAME))
CONFIG_PATH = CONFIG_DIR / 'config.json'

WINDOWS = os.name == 'nt'

LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s]\n%(message)s'
# TODO: command line or config option to change log path
LOG_PATH_FORMAT = 'logs/{0:%Y}/{0:%m}/{0:%Y-%m-%d_%H-%M}.log'
LOG_PATH = CONFIG_DIR / LOG_PATH_FORMAT.format(datetime.datetime.now())

# TODO: better means of fetching common user agents
USER_AGENTS = {
    'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.86 Safari/537.36',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:42.0) Gecko/20100101 Firefox/42.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.7 (KHTML, like Gecko) Version/9.0.1 Safari/601.2.7'
}
EXCEPTION_GUIS = {'Qt5', 'notify-send'}
DEFAULT_EXCEPTION_GUI = None
HAS_NOTIFY_SEND = shutil.which('notify-send') is not None
DEFAULT_FEED_ENABLED = DEFAULT_SUBSCRIPTION_ENABLED = True
DEFAULT_DIRECTORY = pathlib.Path(tempfile.gettempdir())
# click.launch uses os.system on Windows, which shows a cmd.exe window for a split second.
# hence os.startfile is preferred for that platform.
DEFAULT_COMMAND = startfile = os.startfile if WINDOWS else click.launch
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

        self.user_agent = (self.json_dict['user_agent'] if 'user_agent' in self.json_dict
                           else random.choice(USER_AGENTS))

        self.exception_gui = self.json_dict.get('exception_gui', DEFAULT_EXCEPTION_GUI)
        if self.exception_gui == 'Qt5' and self.pyqt_qapplication is None:
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
                text = '{} encountered {!r} exception.'.format(NAME, type(exception))
                error_traceback = traceback.format_exc()
                if self.exception_gui == 'notify-send':
                    self.show_error_notification(text, error_traceback)
                elif self.exception_gui == 'Qt5':
                    self.show_pyqt5_error_messagebox(text, error_traceback)
            raise

    @staticmethod
    def show_error_pyqt5_messagebox(text, error_traceback):
        import PyQt5.QtWidgets

        messagebox = PyQt5.QtWidgets.QMessageBox()
        messagebox.setWindowTitle(NAME)
        messagebox.setText(text)
        messagebox.setDetailedText(error_traceback)

        ok_button = messagebox.addButton(messagebox.Ok)
        open_button = messagebox.addButton('Open Log', messagebox.ActionRole)
        messagebox.setDefaultButton(ok_button)

        messagebox.exec_()
        if messagebox.clickedButton() == open_button:
            startfile(log_path)

    @staticmethod
    def show_error_notification(text):
        message = '{} Click to open log file:\n{}'.format(text, log_path.as_uri())
        subprocess.Popen(['notify-send', '--app-name', NAME, NAME, message])

    def run(self):
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
                    torrent_path = subscription.download(entry)
                    logging.info('%r downloaded to %r', entry.link, torrent_path)
                    subscription.command(torrent_path)
                    logging.info('%r launched with %r', torrent_path, subscription.command)
                    if subscription.has_lower_number_than(number):
                        subscription.number = number

class Command:
    path_replacement_regex = re.compile(re.escape(COMMAND_PATH_ARGUMENT))

    def __init__(self, arguments):
        self.arguments = arguments

    def __repr__(self):
        return '{}(arguments={})'.format(type(self).__name__, self.arguments)

    def __call__(self, path):
        if WINDOWS:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None
        arguments = [self.path_replacement_regex.sub(path, argument)
                     for argument in self.arguments]
        return subprocess.Popen(arguments, startupinfo=startupinfo)

class Feed:
    def __init__(self, name, url, enabled, subscriptions):
        self.name = name
        self.url = url
        self.enabled = enabled
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
        logging.info('Feed %r: parsed url %r', self.name, self.url)
        return rss

    def enabled_subscriptions(self):
        for subscription in self.subscriptions.values():
            if subscription.enabled:
                yield subscription

    def matching_subscriptions(self):
        rss = self.fetch()
        for subscription in self.enabled_subscriptions():
            logging.debug('Checking entries against subscription %r', subscription.name)
            for index, entry in enumerate(rss.entries):
                match = subscription.regex.search(entry.title)
                if match:
                    number = pkg_resources.parse_version(match.group('number'))
                    if subscription.has_lower_number_than(number):
                        logging.info('MATCH: Entry %s titled %r has greater number than '
                                     'subscription %r; yielded: %s > %s', index, entry.title,
                                     subscription.name, number, subscription.number)
                        yield subscription, entry, number
                    else:
                        logging.debug('NO MATCH: Entry %s titled %r matches but number is smaller '
                                      'than or equal to that of subscription %r; skipped: %s <= %s',
                                      index, entry.title, subscription.name,
                                      number, subscription.number)
                else:
                    logging.debug('NO MATCH: Entry %s titled %r does not match subscription %r',
                                  index, entry.title, subscription.name)

    def has_any_enabled_subscriptions(self):
        try:
            next(self.enabled_subscriptions())
            return True
        except StopIteration:
            return False

class Subscription:
    windows_forbidden_characters_regex = re.compile(r'[\\/:\*\?"<>\| ]')

    def __init__(self, name, regex, directory, command, enabled):
        self.name = name
        self.regex = regex
        self.directory = directory
        self.command = command
        self.enabled = enabled

        self.number_file_path = self.windows_safe_filename(CONFIG_DIR / self.name+'.number')
        self._number = None

    def __repr__(self):
        return ('{}(name={!r}, feed={!r}, pattern={!r}, directory={!r}, command={!r}, number={})'
                .format(type(self).__name__, self.name, self.feed.name,
                        self.regex.pattern, self.directory, self.command, self.number))

    def windows_safe_filename(self, path):
        if WINDOWS:
            new_name = self.windows_forbidden_characters_regex.sub('_', path.name)
            return path.with_name(new_name)
        return path

    @property
    def number(self):
        if self._number is None:
            try:
                with self.number_file_path.open() as file:
                    line = file.readline()
                self._number = pkg_resources.parse_version(line)
                logging.info('Parsed %r; returning %s', self.number_file_path, self._number)
            except FileNotFoundError:
                logging.info('No number file found at %r; returning None', self.number_file_path)
        return self._number

    @number.setter
    def number(self, new_number):
        self._number = new_number
        self.number_file_path.write_text(str(new_number))
        logging.info('Number %s written to file %r', new_number, self.number_file_path)

    def has_lower_number_than(self, other_number):
        return self.number is None or self.number < other_number

    @staticmethod
    def torrent_link_for(rss_entry):
        for link in rss_entry.links:
            if link.type == TORRENT_MIMETYPE:
                logging.debug('First link of entry %r with mimetype %r: %s',
                              rss_entry.title, TORRENT_MIMETYPE, link.href)
                return link.href
        logging.info('Entry %r has no link with mimetype %r; returning first link: %s',
                     rss_entry.title, TORRENT_MIMETYPE, rss_entry.link)
        return rss_entry.link

    def download(self, rss_entry):
        link = self.torrent_link_for(rss_entry)
        headers = {} if self.user_agent is None else {'User-Agent': self.user_agent}
        logging.debug('Sending GET request to %r with headers %s', link, headers)
        response = requests.get(link, headers=headers)
        logging.debug("Response status code is %s, 'ok' is %s", response.status_code, response.ok)
        response.raise_for_status()

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.windows_safe_filename(self.directory / rss_entry.title+'.torrent')
        path.write_bytes(response.content)
        logging.debug('Wrote response content to %r', path)
        return path

def configure_logging(file_logging_level, console_logging_level):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_logging_level)

    file_handler = logging.FileHandler(str(LOG_PATH))
    file_handler.setLevel(file_logging_level)

    logging.basicConfig(format=LOG_MESSAGE_FORMAT, handlers=[file_handler, console_handler])

@contextlib.contextmanager
def exception_logging():
    try:
        yield
    except Exception as error:
        logging.exception('%r', type(error))
        raise

def logging_level_from_string(context, parameter, value):
    return getattr(logging, value)

logging_level_choice = click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

@click.command()
@click.option('--file-logging-level', type=logging_level_choice, default='DEBUG',
              callback=logging_level_from_string)
@click.option('--console-logging-level', type=logging_level_choice, default='INFO',
              callback=logging_level_from_string)
@click.version_option()
def main(file_logging_level, console_logging_level):
    configure_logging(file_logging_level, console_logging_level)

    with exception_logging():
        try:
            config = Config()
        except FileNotFoundError as error:
            raise click.Abort('No config file found at {!r}. See the schema in the package.'
                              .format(CONFIG_PATH)) from error
        with config.errors_shown_as_gui():
            config.run()
