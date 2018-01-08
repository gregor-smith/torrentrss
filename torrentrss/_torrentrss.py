import os
import re
import sys
import json
import shutil
import hashlib
import logging
import tempfile
import contextlib
import subprocess
import collections
from pathlib import Path
from typing.re import Pattern, Match
from typing import (Any, Dict, List, Tuple, Union,
                    Iterator, Optional, ClassVar, NamedTuple)

import click
import easygui
import requests
import feedparser
import jsonschema
import pkg_resources

NAME = 'torrentrss'
VERSION = '0.5.3'

WINDOWS = os.name == 'nt'

CONFIG_DIRECTORY = Path(click.get_app_dir(NAME))
CONFIG_PATH: Path = CONFIG_DIRECTORY.joinpath('config.json')
CONFIG_SCHEMA_FILENAME = 'config_schema.json'

LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'

TEMPORARY_DIRECTORY = Path(tempfile.gettempdir())
COMMAND_PATH_ARGUMENT = '$PATH_OR_URL'
TORRENT_MIMETYPE = 'application/x-bittorrent'


Json = Dict[str, Any]
PathOrUrl = Union[Path, str]


class TorrentRSSError(Exception):
    pass


class ConfigError(TorrentRSSError):
    pass


class FeedError(TorrentRSSError):
    pass


class Config(collections.OrderedDict):
    _exception_gui: Optional[str] = None

    path: Path
    json_dict: Json
    default_directory: Path
    default_command: 'Command'

    def __init__(self, path: Path=CONFIG_PATH) -> None:
        super().__init__()
        self.path = path

        with path.open(encoding='utf-8') as file:
            self.json_dict = json.load(
                file, object_pairs_hook=collections.OrderedDict
            )
        jsonschema.validate(self.json_dict, self.get_schema_dict())

        self.exception_gui = self.json_dict.get('exception_gui')

        with self.exceptions_shown_as_gui():
            self.default_directory = (
                Path(self.json_dict['default_directory'])
                if 'default_directory' in self.json_dict else
                TEMPORARY_DIRECTORY
            )
            self.default_command = Command(
                self.json_dict.get('default_command'),
                self.json_dict.get('default_command_shell_enabled', False)
            )

            self.update((name, Feed(config=self, name=name, **feed_dict))
                        for name, feed_dict in self.json_dict['feeds'].items())

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(path={self.path!r})'

    @staticmethod
    def get_schema() -> str:
        schema: bytes = pkg_resources.resource_string(__name__,
                                                      CONFIG_SCHEMA_FILENAME)
        return str(schema, encoding='utf-8') \
            .replace('\r\n', '\n')

    @classmethod
    def get_schema_dict(cls) -> Json:
        return json.loads(cls.get_schema())

    @property
    def exception_gui(self) -> Optional[str]:
        return self._exception_gui

    @exception_gui.setter
    def exception_gui(self, value: Optional[str]) -> None:
        if value == 'notify-send' and shutil.which('notify-send') is None:
            raise ConfigError("'exception_gui' is 'notify-send' but it "
                              'could not be found on the PATH')
        elif value != 'easygui' and value is not None:
            raise ConfigError(f"'exception_gui' {value!r} unknown. "
                              "Must be 'notify-send' or 'easygui'")
        self._exception_gui = value

    @staticmethod
    def show_notify_send_exception_gui() -> subprocess.Popen:
        text = f'An exception of type {sys.last_type.__name__} occurred.'
        return subprocess.Popen(['notify-send', '--app-name',
                                 NAME, NAME, text])

    @staticmethod
    def show_easygui_exception_gui() -> None:
        text = f'An exception of type {sys.last_type.__name__} occurred.'
        easygui.exceptionbox(msg=text, title=NAME)

    @contextlib.contextmanager
    def exceptions_shown_as_gui(self) -> contextlib._GeneratorContextManager:
        try:
            yield
        except Exception:
            if self.exception_gui == 'notify-send':
                self.show_notify_send_exception_gui()
            elif self.exception_gui == 'easygui':
                self.show_easygui_exception_gui()
            raise

    def enabled_feeds(self) -> Iterator['Feed']:
        return (feed for feed in self.values() if feed.enabled)

    def check_feeds(self) -> None:
        with self.exceptions_shown_as_gui():
            for feed in self.enabled_feeds():
                for sub, entry, number in feed.matching_subs():
                    path_or_url: PathOrUrl = feed.download_entry(entry,
                                                                 sub.directory)
                    sub.command(path_or_url)
                    if number > sub.number:
                        sub.number = number

    def save_new_episode_numbers(self) -> None:
        logging.info("Writing episode numbers to '%s'", self.path)
        json_feeds: Json = self.json_dict['feeds']
        for feed_name, feed in self.items():
            json_subs: Json = json_feeds[feed_name]['subscriptions']
            for sub_name, sub in feed.items():
                sub_dict: Json = json_subs[sub_name]
                if sub.number.series is not None:
                    sub_dict['series_number'] = sub.number.series
                if sub.number.episode is not None:
                    sub_dict['episode_number'] = sub.number.episode
        with self.path.open('w', encoding='utf-8') as file:
            json.dump(self.json_dict, file, indent='\t')


class Command:
    path_substitution_regex: ClassVar[Pattern] \
        = re.compile(re.escape(COMMAND_PATH_ARGUMENT))

    arguments: Optional[List[str]]
    shell: bool

    def __init__(self, arguments: Optional[List[str]]=None,
                 shell: bool=False) -> None:
        self.arguments = arguments
        self.shell = shell

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(arguments={self.arguments})'

    def subbed_arguments(self, path_or_url: PathOrUrl) -> Iterator[str]:
        # The repl parameter here is a function which at first looks like it
        # could just be a string, but it actually needs to be a function or
        # else escapes in the string would be processed, leading to problems
        # when dealing with file paths, for example.
        # See: https://docs.python.org/3/library/re.html#re.sub
        #      https://stackoverflow.com/a/16291763/3289208
        def replacer(match: Match) -> str:
            return os.fspath(path_or_url)
        for argument in self.arguments:
            yield self.path_substitution_regex.sub(replacer, argument)

    @staticmethod
    def startfile(path_or_url: PathOrUrl) -> None:
        # click.launch uses os.system on Windows, which shows a cmd.exe
        # window for a split second. Hence os.startfile is preferred.
        path = (str(path_or_url) if isinstance(path_or_url, Path)
                else path_or_url)
        if WINDOWS:
            return os.startfile(path)
        click.launch(path)

    def __call__(self, path_or_url: PathOrUrl) -> Optional[subprocess.Popen]:
        if self.arguments is not None:
            if WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
            else:
                startupinfo = None
            arguments: List[str] = list(self.subbed_arguments(path_or_url))
            logging.info('Launching subprocess with arguments %s', arguments)
            return subprocess.Popen(arguments, shell=self.shell,
                                    startupinfo=startupinfo)
        logging.info("Launching %r with default program", path_or_url)
        self.startfile(path_or_url)


class Feed(collections.OrderedDict):
    windows_forbidden_characters_regex: ClassVar[Pattern] \
        = re.compile(r'[\\/:\*\?"<>\|]')

    _enabled: bool

    config: Config
    name: str
    url: str
    user_agent: Optional[str]
    magnet_enabled: bool
    torrent_url_enabled: bool
    torrent_file_enabled: bool
    hide_torrent_filename_enabled: bool

    def __init__(self, config: Config, name: str,
                 url: str, subscriptions: Json,
                 user_agent: Optional[str]=None, enabled: bool=True,
                 magnet_enabled: bool=True, torrent_url_enabled: bool=True,
                 torrent_file_enabled: bool=True,
                 hide_torrent_filename_enabled: bool=True) -> None:
        if not any([magnet_enabled, torrent_url_enabled,
                    torrent_file_enabled]):
            raise ConfigError(f'Feed {name!r}: at least one of '
                              "'magnet_enabled', 'torrent_url_enabled', or "
                              "'torrent_file_enabled' must be true")

        self._enabled = False

        self.config = config
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.enabled = enabled
        self.magnet_enabled = magnet_enabled
        self.torrent_url_enabled = torrent_url_enabled
        self.torrent_file_enabled = torrent_file_enabled
        self.hide_torrent_filename_enabled = hide_torrent_filename_enabled

        self.update((name, Subscription(feed=self, name=name, **sub_dict))
                    for name, sub_dict in subscriptions.items())

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}(name={self.name!r}, '
                f'url={self.url!r}, subs={list(self.keys())})')

    @property
    def enabled(self) -> bool:
        return self._enabled and any(self.enabled_subs())

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def fetch(self) -> feedparser.FeedParserDict:
        rss: feedparser.FeedParserDict = feedparser.parse(self.url)
        if rss['bozo']:
            raise FeedError(f'Feed {self.name!r}: error parsing '
                            f'url {self.url!r}') from rss['bozo_exception']
        logging.info('Feed %r: downloaded url %r', self.name, self.url)
        return rss

    def enabled_subs(self) -> Iterator['Subscription']:
        return (sub for sub in self.values() if sub.enabled)

    def matching_subs(self) -> Iterator[Tuple['Subscription',
                                              feedparser.FeedParserDict,
                                              'EpisodeNumber']]:
        rss: feedparser.FeedParserDict = self.fetch()
        for sub in self.enabled_subs():
            logging.debug('Sub %r: checking entries against pattern: %s',
                          sub.name, sub.regex.pattern)
            sub_number: EpisodeNumber = sub.number
            for index, entry in enumerate(rss['entries']):
                match: Match = sub.regex.search(entry['title'])
                if match:
                    number = EpisodeNumber.from_regex_match(match)
                    if number > sub_number:
                        logging.info('MATCH: entry %s %r has greater number '
                                     'than sub %r: %s > %s',
                                     index, entry['title'], sub.name,
                                     number, sub.number)
                        yield sub, entry, number
                    else:
                        logging.debug('NO MATCH: entry %s %r matches but '
                                      'number less than or equal to sub %r: '
                                      '%s <= %s', index, entry['title'],
                                      sub.name, number, sub.number)
                else:
                    logging.debug('NO MATCH: entry %s %r against sub %r',
                                  index, entry['title'], sub.name)

    @staticmethod
    def torrent_url_for_entry(rss_entry: feedparser.FeedParserDict) -> str:
        for link in rss_entry['links']:
            if link['type'] == TORRENT_MIMETYPE:
                href: str = link['href']
                logging.debug('Entry %r: first link with mimetype %r is %r',
                              rss_entry['title'], TORRENT_MIMETYPE, href)
                return href
        link = rss_entry['link']
        logging.info('Entry %r: no link with mimetype %r, returning first '
                     'link %r', rss_entry['title'], TORRENT_MIMETYPE, link)
        return link

    @staticmethod
    def magnet_uri_for_entry(rss_entry: feedparser.FeedParserDict) -> str:
        try:
            magnet: str = rss_entry['torrent_magneturi']
            logging.debug('Entry %r: has magnet url %r',
                          rss_entry['title'], magnet)
            return magnet
        except KeyError:
            logging.info("Entry %r: 'magnet_enabled' is true but no "
                         'magnet link could be found', rss_entry['title'])
            raise

    def download_entry_torrent_file(self, url: str,
                                    rss_entry: feedparser.FeedParserDict,
                                    directory: Path) -> Path:
        headers: Dict[str, str] = ({} if self.user_agent is None else
                                   {'User-Agent': self.user_agent})
        logging.debug('Feed %r: sending GET request to %r with headers %s',
                      self.name, url, headers)
        response: requests.Response = requests.get(url, headers=headers)
        logging.debug("Feed %r: response status code is %s, 'ok' is %s",
                      self.name, response.status_code, response.ok)
        response.raise_for_status()

        title: str = (hashlib.sha3_224(response.content).hexdigest()
                      if self.hide_torrent_filename_enabled else
                      rss_entry['title'])
        path: Path = directory.joinpath(title) \
            .with_suffix('.torrent')
        if WINDOWS:
            new_name: str = self.windows_forbidden_characters_regex.sub(
                '_', path.name
            )
            path = path.with_name(new_name)

        directory.mkdir(parents=True, exist_ok=True)
        logging.debug("Feed %r: writing response bytes to file '%s'",
                      self.name, path)
        path.write_bytes(response.content)
        return path

    def download_entry(self, rss_entry: feedparser.FeedParserDict,
                       directory: Path) -> PathOrUrl:
        if self.magnet_enabled:
            with contextlib.suppress(KeyError):
                return self.magnet_uri_for_entry(rss_entry)

        url: str = self.torrent_url_for_entry(rss_entry)
        if self.torrent_url_enabled:
            logging.debug('Feed %r: returning torrent url %r', self.name, url)
            return url

        if not self.torrent_file_enabled:
            if self.magnet_enabled:
                message = ("'magnet_enabled' is true but it failed, and"
                           "'torrent_url_enabled' and 'torrent_file_enabled' "
                           'are false.')
            else:
                message = ("'magnet_enabled', 'torrent_url_enabled', and "
                           "'torrent_file_enabled' are all false.")
            raise FeedError(f'Feed {self.name!r}: {message}'
                            'Nothing to download.')

        try:
            return self.download_entry_torrent_file(url, rss_entry, directory)
        except Exception as error:
            raise FeedError(f'Feed {self.name!r}: failed to download {url}') \
                from error


class EpisodeNumber(NamedTuple('EpisodeNumberBase',
                               series=Optional[int], episode=Optional[int])):
    def __gt__(self, other: Tuple[Optional[int], Optional[int]]) -> bool:
        series, episode = other
        if self.episode is None:
            return False
        return episode is None \
            or (self.series is not None and series is not None
                and self.series > series) \
            or self.episode > episode

    @classmethod
    def from_regex_match(cls, match: Match) -> 'EpisodeNumber':
        groups: Dict[str, str] = match.groupdict()
        series: Optional[int] = (int(groups['series'])
                                 if 'series' in groups else None)
        return cls(series=series, episode=int(groups['episode']))


class Subscription:
    _regex: Optional[Pattern]
    _directory: Optional[Path]
    _command: Optional[Command]

    feed: Feed
    name: str
    number: EpisodeNumber
    enabled: bool

    def __init__(self, feed: Feed, name: str, pattern: str,
                 series_number: Optional[int]=None,
                 episode_number: Optional[int]=None,
                 directory: Optional[str]=None,
                 command: Optional[List[str]]=None,
                 command_shell_enabled: bool=False,
                 enabled: bool=True) -> None:
        self._regex = self._directory = self._command = None

        self.feed = feed
        self.name = name
        try:
            self.regex = re.compile(pattern)
        except re.error as error:
            raise ConfigError(f'Feed {feed.name!r} sub {self.name!r} pattern '
                              f'{pattern!r} not valid regex: '
                              f'{", ".join(error.args)}') from error
        self.number = EpisodeNumber(series=series_number,
                                    episode=episode_number)
        if directory is not None:
            self.directory = Path(directory)
        if command is not None:
            self.command = Command(arguments=command,
                                   shell=command_shell_enabled)
        self.enabled = enabled

    @property
    def config(self) -> Config:
        return self.feed.config

    @property
    def regex(self) -> Optional[Pattern]:
        return self._regex

    @regex.setter
    def regex(self, value: Pattern):
        if 'episode' not in value.groupindex:
            raise ConfigError(f'Feed {self.feed.name!r} sub {self.name!r} '
                              f'pattern {value!r} has no group for the '
                              'episode number')
        self._regex = value

    @property
    def directory(self) -> Path:
        return self._directory or self.config.default_directory

    @directory.setter
    def directory(self, value: Path):
        self._directory = value

    @property
    def command(self) -> Command:
        return self._command or self.config.default_command

    @command.setter
    def command(self, value: Command):
        self._command = value

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}(name={self.name!r}, '
                f'pattern={self.regex.pattern!r}, '
                f'directory={self.directory!r}, command={self.command!r}, '
                f'enabled={self.enabled}, number={self.number})')


def configure_logging(level: Optional[str]=None) -> None:
    logging.basicConfig(format=LOG_MESSAGE_FORMAT, level=level)

    # silence requests' logging in all but the worst cases
    logging.getLogger('requests') \
        .setLevel(logging.WARNING)
    logging.getLogger('urllib3') \
        .setLevel(logging.WARNING)


def print_schema(context: click.Context, parameter: click.Parameter,
                 value: Any) -> None:
    if value:
        print(Config.get_schema())
        context.exit()


@click.command()
@click.option('--logging-level', default='DEBUG', show_default=True,
              type=click.Choice(['DISABLE', 'DEBUG', 'INFO',
                                 'WARNING', 'ERROR', 'CRITICAL']))
@click.option('--print-config-schema', is_flag=True, is_eager=True,
              expose_value=False, callback=print_schema)
@click.version_option(VERSION)
def main(logging_level: str) -> None:
    configure_logging(level=logging_level)

    try:
        try:
            config = Config()
        except FileNotFoundError as error:
            raise click.Abort(
                f'No config file found at {str(CONFIG_PATH)!r}. '
                "See '--print-config-schema' for reference."
            ) from error
        config.check_feeds()
        config.save_new_episode_numbers()
    except Exception as error:
        logging.exception(error.__class__.__name__)
        raise
    finally:
        logging.shutdown()
