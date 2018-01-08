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
from pathlib import Path
from collections import OrderedDict
from typing.re import Pattern, Match
from typing import (Any, Dict, List, Tuple, Union,
                    Generator, Iterator, Optional, ClassVar)

import click
import requests
import feedparser
from feedparser import FeedParserDict
import jsonschema
import pkg_resources

NAME = 'torrentrss'
VERSION = '0.5.3'
WINDOWS = os.name == 'nt'
CONFIG_DIRECTORY = Path(click.get_app_dir(NAME))
CONFIG_PATH = Path(CONFIG_DIRECTORY, 'config.json')
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


def get_schema() -> str:
    return pkg_resources.resource_string(__name__, CONFIG_SCHEMA_FILENAME) \
        .decode('utf-8')


def get_schema_dict() -> Json:
    return json.loads(get_schema())


@contextlib.contextmanager
def exceptions_shown_as_gui() -> Generator:
    try:
        yield
    except Exception:
        text = f'An exception of type {sys.last_type.__name__} occurred.'
        if shutil.which('notify-send') is not None:
            subprocess.Popen(['notify-send', '--app-name', NAME, NAME, text])
            return
        with contextlib.suppress(ImportError):
            from easygui import exceptionbox
            exceptionbox(msg=text, title=NAME)
            return
        raise


def startfile(path_or_url: PathOrUrl) -> None:
    # click.launch uses os.system on Windows, which shows a cmd.exe
    # window for a split second. Hence os.startfile is preferred.
    if WINDOWS:
        return os.startfile(path_or_url)
    click.launch(os.fspath(path_or_url))


class TorrentRSS:
    _json: Json

    feeds: Dict[str, 'Feed']
    config_path: Path
    default_directory: Path
    default_command: 'Command'

    def __init__(self) -> None:
        with open(CONFIG_PATH, encoding='utf-8') as file:
            self._json = json.load(file, object_pairs_hook=OrderedDict)
        jsonschema.validate(self._json, get_schema_dict())

        self.default_directory = Path(
            self._json.get('default_directory', TEMPORARY_DIRECTORY)
        )
        self.default_command = Command(
            self._json.get('default_command'),
            self._json.get('default_command_shell_enabled', False)
        )
        self.feeds = OrderedDict(
            (name, Feed(config=self, name=name, **feed_dict))
            for name, feed_dict in self._json['feeds'].items()
        )

    def check_feeds(self) -> None:
        for feed in self.feeds.values():
            if not feed.enabled:
                continue
            for sub, entry, number in feed.matching_subs():
                path_or_url = feed.download_entry(entry, sub.directory)
                sub.command(path_or_url)
                if number > sub.number:
                    sub.number = number

    def save_episode_numbers(self) -> None:
        logging.info("Writing episode numbers to '%s'", CONFIG_PATH)
        json_feeds = self._json['feeds']
        for feed_name, feed in self.feeds.items():
            json_subs = json_feeds[feed_name]['subscriptions']
            for sub_name, sub in feed.subscriptions.items():
                sub_dict = json_subs[sub_name]
                if sub.number.series is not None:
                    sub_dict['series_number'] = sub.number.series
                sub_dict['episode_number'] = sub.number.episode
        with open(CONFIG_PATH, mode='w', encoding='utf-8') as file:
            json.dump(self._json, file, indent=4)


class Command:
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
            yield re.sub(pattern=re.escape(COMMAND_PATH_ARGUMENT),
                         repl=replacer, string=argument)

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
        startfile(path_or_url)


class Feed:
    _enabled: bool

    config: TorrentRSS
    subscriptions: Dict[str, 'Subscription']
    name: str
    url: str
    user_agent: Optional[str]
    use_magnet: bool
    use_torrent_url: bool
    use_torrent_file: bool
    hide_torrent_filename: bool

    def __init__(self, config: TorrentRSS, name: str,
                 url: str, subscriptions: Json,
                 user_agent: Optional[str]=None, enabled: bool=True,
                 use_magnet: bool=True, use_torrent_url: bool=True,
                 use_torrent_file: bool=True,
                 hide_torrent_filename: bool=True) -> None:
        if not use_magnet and not use_torrent_url and not use_torrent_file:
            raise ConfigError(
                f"Feed {name!r}: at least one of 'use_magnet', "
                "'use_torrent_url', or 'use_torrent_file' must be true"
            )

        self._enabled = False

        self.config = config
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.enabled = enabled
        self.use_magnet = use_magnet
        self.use_torrent_url = use_torrent_url
        self.use_torrent_file = use_torrent_file
        self.hide_torrent_filename = hide_torrent_filename

        self.subscriptions = OrderedDict(
            (name, Subscription(feed=self, name=name, **sub_dict))
            for name, sub_dict in subscriptions.items()
        )

    def __repr__(self):
        return (f'{self.__class__.__name__}(enabled={self.enabled}, '
                f'name={self.name}, url={self.url})')

    @property
    def enabled(self) -> bool:
        return self._enabled and any(sub.enabled for sub in
                                     self.subscriptions.values())

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def fetch(self) -> FeedParserDict:
        rss = feedparser.parse(self.url)
        if rss['bozo']:
            raise FeedError(f'Feed {self.name!r}: error parsing '
                            f'url {self.url!r}') \
                from rss['bozo_exception']
        logging.info('Feed %r: downloaded url %r', self.name, self.url)
        return rss

    def matching_subs(self) -> Iterator[Tuple['Subscription',
                                              FeedParserDict,
                                              'EpisodeNumber']]:
        rss = self.fetch()
        for sub in self.subscriptions.values():
            if not sub.enabled:
                continue
            logging.debug('Sub %r: checking entries against pattern: %s',
                          sub.name, sub.regex.pattern)
            current_number = sub.number
            for index, entry in enumerate(rss['entries']):
                match = sub.regex.search(entry['title'])
                if match:
                    number = EpisodeNumber.from_regex_match(match)
                    if number > current_number:
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
    def torrent_url_for_entry(rss_entry: FeedParserDict) -> str:
        for link in rss_entry['links']:
            if link['type'] == TORRENT_MIMETYPE:
                href = link['href']
                logging.debug('Entry %r: first link with mimetype %r is %r',
                              rss_entry['title'], TORRENT_MIMETYPE, href)
                return href
        link = rss_entry['link']
        logging.info('Entry %r: no link with mimetype %r, returning first '
                     'link %r', rss_entry['title'], TORRENT_MIMETYPE, link)
        return link

    @staticmethod
    def magnet_uri_for_entry(rss_entry: FeedParserDict) -> str:
        try:
            magnet = rss_entry['torrent_magneturi']
            logging.debug('Entry %r: has magnet url %r',
                          rss_entry['title'], magnet)
            return magnet
        except KeyError:
            logging.info("Entry %r: 'use_magnet' is true but no "
                         'magnet link could be found', rss_entry['title'])
            raise

    def download_entry_torrent_file(self, url: str,
                                    rss_entry: FeedParserDict,
                                    directory: Path) -> Path:
        headers = ({} if self.user_agent is None else
                   {'User-Agent': self.user_agent})
        logging.debug('Feed %r: sending GET request to %r with headers %s',
                      self.name, url, headers)
        response = requests.get(url, headers=headers)
        logging.debug("Feed %r: response status code is %s, 'ok' is %s",
                      self.name, response.status_code, response.ok)
        response.raise_for_status()

        title = (hashlib.sha256(response.content).hexdigest()
                 if self.hide_torrent_filename else
                 rss_entry['title'])
        path = Path(directory, title).with_suffix('.torrent')
        if WINDOWS:
            new_name = re.sub(pattern=r'[\\/:\*\?"<>\|]', repl='_',
                              string=path.name)
            path = path.with_name(new_name)

        directory.mkdir(parents=True, exist_ok=True)
        logging.debug("Feed %r: writing response bytes to file '%s'",
                      self.name, path)
        path.write_bytes(response.content)
        return path

    def download_entry(self, rss_entry: FeedParserDict,
                       directory: Path) -> PathOrUrl:
        if self.use_magnet:
            with contextlib.suppress(KeyError):
                return self.magnet_uri_for_entry(rss_entry)

        url = self.torrent_url_for_entry(rss_entry)
        if self.use_torrent_url:
            logging.debug('Feed %r: returning torrent url %r', self.name, url)
            return url

        if not self.use_torrent_file:
            if self.use_magnet:
                message = ("'use_magnet' is true but it failed, and"
                           "'use_torrent_url' and 'use_torrent_file' "
                           'are false.')
            else:
                message = ("'use_magnet', 'use_torrent_url', and "
                           "'use_torrent_file' are all false.")
            raise FeedError(f'Feed {self.name!r}: {message} '
                            'Nothing to download.')

        try:
            return self.download_entry_torrent_file(url, rss_entry, directory)
        except Exception as error:
            raise FeedError(f'Feed {self.name!r}: failed to download {url}') \
                from error


class EpisodeNumber:
    series: Optional[int]
    episode: Optional[int]

    def __init__(self, series: Optional[int], episode: Optional[int]) -> None:
        self.series = series
        self.episode = episode

    def __repr__(self):
        return (f'{self.__class__.__name__}(series={self.series}, '
                f'episode={self.episode})')

    @classmethod
    def from_regex_match(cls, match: Match) -> 'EpisodeNumber':
        groups = match.groupdict()
        return cls(
            series=int(groups['series']) if 'series' in groups else None,
            episode=int(groups['episode'])
        )

    def __gt__(self, other: 'EpisodeNumber') -> bool:
        if self.episode is None:
            return False
        return other.episode is None \
            or (self.series is not None and other.series is not None
                and self.series > other.series) \
            or self.episode > other.episode


class Subscription:
    _directory: Optional[Path]
    _command: Optional[Command]

    feed: Feed
    name: str
    regex: Pattern
    number: EpisodeNumber
    enabled: bool

    def __init__(self, feed: Feed, name: str, pattern: str,
                 series_number: Optional[int]=None,
                 episode_number: Optional[int]=None,
                 directory: Optional[str]=None,
                 command: Optional[List[str]]=None,
                 use_shell_for_command: bool=False,
                 enabled: bool=True) -> None:
        self._directory = self._command = None

        self.feed = feed
        self.name = name
        try:
            self.regex = re.compile(pattern)
        except re.error as error:
            args = ", ".join(error.args)
            raise ConfigError(f'Feed {feed.name!r} sub {name!r} pattern '
                              f'{pattern!r} not valid regex: {args}') \
                from error
        if 'episode' not in self.regex.groupindex:
            raise ConfigError(f'Feed {feed.name!r} sub {name!r} pattern '
                              f'{pattern!r} has no group for the episode '
                              'number')

        self.number = EpisodeNumber(series=series_number,
                                    episode=episode_number)
        if directory is not None:
            self.directory = Path(directory)
        if command is not None:
            self.command = Command(arguments=command,
                                   shell=use_shell_for_command)
        self.enabled = enabled

    def __repr__(self):
        return (f'{self.__class__.__name__}(enabled={self.enabled}, '
                f'name={self.name}, feed={self.feed.name})')

    @property
    def config(self) -> TorrentRSS:
        return self.feed.config

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
        print(get_schema())
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
            config = TorrentRSS()
        except FileNotFoundError as error:
            raise click.Abort(f'No config file found at {str(CONFIG_PATH)!r}. '
                              "See '--print-config-schema' for reference.") \
                from error
        config.check_feeds()
        config.save_episode_numbers()
    except Exception as error:
        logging.exception(error.__class__.__name__)
        raise
    finally:
        logging.shutdown()
