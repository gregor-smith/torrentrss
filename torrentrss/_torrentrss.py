from __future__ import annotations

import os
import re
import sys
import json
import shutil
import subprocess
from os import PathLike
from pathlib import Path
from argparse import ArgumentParser
from typing.re import Pattern, Match
from logging import Formatter, Logger, StreamHandler
from typing import (
    Any,
    Dict,
    List,
    Tuple,
    Iterator,
    Optional,
    AsyncIterator,
    TextIO,
    cast
)

import appdirs
import feedparser
import jsonschema
from aiofile import AIOFile
from feedparser import FeedParserDict
from aiohttp.client import ClientSession


NAME = 'torrentrss'
VERSION = '0.8'
CONFIG_PATH = Path(
    appdirs.user_config_dir(appname=NAME, roaming=True),
    'config.json'
)
CONFIG_SCHEMA_FILENAME = 'config_schema.json'
LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'
COMMAND_URL_ARGUMENT = '$URL'
TORRENT_MIMETYPE = 'application/x-bittorrent'
WINDOWS = sys.platform == 'win32' or sys.platform == 'cygwin'


Json = Dict[str, Any]


logger = Logger(name=NAME)


class TorrentRSSError(Exception):
    pass


class ConfigError(TorrentRSSError):
    pass


class FeedError(TorrentRSSError):
    pass


async def read_text(path: PathLike) -> str:
    async with AIOFile(path) as file:
        return await file.read()


async def write_text(path: PathLike, text: str) -> None:
    async with AIOFile(path, mode='w') as file:
        await file.write(text)
        await file.fsync()


async def get_schema() -> str:
    path = Path(__file__).with_name('config_schema.json')
    return await read_text(path)


async def get_schema_dict() -> Json:
    text = await get_schema()
    return json.loads(text)


def show_exception_notification(exception: Exception) -> None:
    if shutil.which('notify-send') is None:
        return
    text = f'An exception of type {exception.__class__.__name__} occurred.'
    subprocess.Popen(
        args=['notify-send', '--app-name', NAME, NAME, text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


class TorrentRSS:
    path: PathLike
    config: Json
    feeds: Dict[str, Feed]
    default_command: Command

    def __init__(self, path: PathLike, config: Json) -> None:
        self.path = path
        self.config = config
        self.default_command = Command(config.get('default_command'))

        default_user_agent = config.get('default_user_agent')
        self.feeds = {
            name: Feed(
                name=name,
                user_agent=feed_dict.pop('user_agent', default_user_agent),
                **feed_dict
            )
            for name, feed_dict in config['feeds'].items()
        }

    @classmethod
    async def from_path(cls, path: PathLike = CONFIG_PATH) -> TorrentRSS:
        config_text = await read_text(path)
        config = json.loads(config_text)
        schema = await get_schema_dict()
        jsonschema.validate(config, schema)

        return cls(path, config)

    async def check_feeds(self) -> None:
        for feed in self.feeds.values():
            async for sub, entry in feed.matching_subs():
                url = await Feed.get_entry_url(entry)
                if sub.command is None:
                    await self.default_command(url)
                else:
                    await sub.command(url)

    # Optional TextIO parameter for saving to a StringIO during testing
    async def save_episode_numbers(self, file: Optional[TextIO] = None) -> None:
        logger.info('Writing episode numbers')

        json_feeds = self.config['feeds']
        for feed_name, feed in self.feeds.items():
            json_subs = json_feeds[feed_name]['subscriptions']
            for sub_name, sub in feed.subscriptions.items():
                sub_dict = json_subs[sub_name]
                if sub.number.series is not None:
                    sub_dict['series_number'] = sub.number.series
                if sub.number.episode is not None:
                    sub_dict['episode_number'] = sub.number.episode

        text = json.dumps(self.config, indent=4)
        if file is None:
            await write_text(self.path, text)
        else:
            file.write(text)

    async def run(self) -> None:
        await self.check_feeds()
        await self.save_episode_numbers()


class Command:
    arguments: Optional[List[str]]

    def __init__(self, arguments: Optional[List[str]] = None) -> None:
        self.arguments = arguments

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(arguments={self.arguments})'

    @staticmethod
    def launch_with_default_application(url: str) -> None:
        if WINDOWS:
            os.startfile(url)
            return
        subprocess.Popen(
            args=['open' if sys.platform == 'darwin' else 'xdg-open', url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def subbed_arguments(self, url: str) -> Iterator[str]:
        # The repl parameter here is a function which at first looks like it
        # could just be a string, but it actually needs to be a function or
        # else escapes in the string would be processed, leading to problems
        # when dealing with file paths, for example.
        # See: https://docs.python.org/3/library/re.html#re.sub
        #      https://stackoverflow.com/a/16291763/3289208
        def replacer(_: Match) -> str:
            return url
        for argument in cast(List[str], self.arguments):
            yield re.sub(
                pattern=re.escape(COMMAND_URL_ARGUMENT),
                repl=replacer,
                string=argument
            )

    async def __call__(self, url: str) -> Optional[subprocess.Popen]:
        if self.arguments is not None:
            startupinfo: Optional[subprocess.STARTUPINFO]
            if WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
            else:
                startupinfo = None

            arguments = list(self.subbed_arguments(url))
            logger.info(
                f'Launching subprocess with arguments {arguments}'
            )
            return subprocess.Popen(
                args=arguments,
                startupinfo=startupinfo
            )

        logger.info(f'Launching {url!r} with default program')
        self.launch_with_default_application(url)
        return None


class Feed:
    subscriptions: Dict[str, Subscription]
    name: str
    url: str
    user_agent: Optional[str]

    def __init__(
        self, *,
        name: str,
        url: str,
        subscriptions: Json,
        user_agent: Optional[str] = None,
    ) -> None:
        self.name = name
        self.url = url
        self.subscriptions = {
            name: Subscription(feed=self, name=name, **sub_dict)
            for name, sub_dict in subscriptions.items()
        }
        self.user_agent = user_agent

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(name={self.name!r}, url={self.url!r})'

    @property
    def headers(self) -> Dict[str, str]:
        if self.user_agent is None:
            return {}
        return {'User-Agent': self.user_agent}

    async def fetch(self) -> FeedParserDict:
        async with ClientSession(headers=self.headers) as session:
            async with session.get(self.url) as response:
                if response.status != 200:
                    raise FeedError(
                        f'Feed {self.name!r}: error sending '
                        + f'request to {self.url!r}'
                    )
                text = await response.text()

        rss = feedparser.parse(text)
        if rss['bozo']:
            raise FeedError(
                f'Feed {self.name!r}: error parsing url {self.url!r}'
            ) from rss['bozo_exception']

        logger.info(f'Feed {self.name!r}: downloaded url {self.url!r}')
        return rss

    async def matching_subs(self) -> AsyncIterator[Tuple[Subscription, FeedParserDict]]:
        if not self.subscriptions:
            return

        rss = await self.fetch()
        # episode numbers are compared against subscriptions' numbers as they
        # were at the beginning of the method rather than comparing to the most
        # recent match. this ensures that all matches in the feed are yielded
        # regardless of whether they are in numeric order.
        original_numbers = {
            sub: sub.number for sub in
            self.subscriptions.values()
        }

        for index, entry in enumerate(reversed(rss['entries'])):
            index = len(rss['entries']) - index - 1
            for sub in self.subscriptions.values():
                match = sub.regex.search(entry['title'])
                if match:
                    number = EpisodeNumber.from_regex_match(match)
                    if number > original_numbers[sub]:
                        logger.info(
                            f'MATCH: entry {index} {entry["title"]!r} has '
                            + f'greater number than sub {sub.name!r}: '
                            + f'{number} > {original_numbers[sub]}'
                        )
                        sub.number = number
                        yield sub, entry
                    else:
                        logger.debug(
                            f'NO MATCH: entry {index} {entry["title"]!r} '
                            + 'matches but number less than or equal to sub '
                            + f'{sub.name!r}: {number} <= '
                            + f'{original_numbers[sub]}'
                        )
                else:
                    logger.debug(
                        f'NO MATCH: entry {index} {entry["title"]!r} against '
                        + f'sub {sub.name!r}'
                    )

    @staticmethod
    async def get_entry_url(rss_entry: FeedParserDict) -> str:
        for link in rss_entry['links']:
            if link['type'] == TORRENT_MIMETYPE:
                logger.debug(
                    f'Entry {rss_entry["title"]!r}: first link with mimetype '
                    + f'{TORRENT_MIMETYPE!r} is {link["href"]!r}'
                )
                return link['href']

        logger.info(
            f'Entry {rss_entry["title"]!r}: no link with mimetype '
            + f'{TORRENT_MIMETYPE!r}, returning first link '
            + f'{rss_entry["link"]!r}'
        )
        return rss_entry['link']


class EpisodeNumber:
    series: Optional[int]
    episode: Optional[int]

    def __init__(self, series: Optional[int], episode: Optional[int]) -> None:
        self.series = series
        self.episode = episode

    @classmethod
    def from_regex_match(cls, match: Match) -> EpisodeNumber:
        groups = match.groupdict()
        return cls(
            series=int(groups['series']) if 'series' in groups else None,
            episode=int(groups['episode'])
        )

    def __gt__(self, other: EpisodeNumber) -> bool:
        if self.episode is None:
            return False
        if other.episode is None:
            return True
        if self.series is not None \
                and other.series is not None \
                and self.series != other.series:
            return self.series > other.series
        return self.episode > other.episode

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(series={self.series}, episode={self.episode})'

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, EpisodeNumber):
            return NotImplemented
        return self.series == other.series and self.episode == other.episode


class Subscription:
    feed: Feed
    name: str
    regex: Pattern
    number: EpisodeNumber
    command: Optional[Command]

    def __init__(
        self,
        feed: Feed,
        name: str,
        pattern: str,
        series_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        command: Optional[List[str]] = None
    ) -> None:
        self.feed = feed
        self.name = name

        try:
            self.regex = re.compile(pattern)
        except re.error as error:
            args = ", ".join(error.args)
            raise ConfigError(
                f'Feed {feed.name!r} sub {name!r} pattern '
                f'{pattern!r} not valid regex: {args}'
            ) from error
        if 'episode' not in self.regex.groupindex:
            raise ConfigError(
                f'Feed {feed.name!r} sub {name!r} pattern '
                f'{pattern!r} has no group for the episode number'
            )

        self.number = EpisodeNumber(
            series=series_number,
            episode=episode_number
        )
        self.command = None if command is None else Command(command)

    def __repr__(self):
        return f'{self.__class__.__name__}name={self.name!r}, feed={self.feed.name!r})'


def configure_logging(level: str) -> None:
    handler = StreamHandler(stream=sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(Formatter(fmt=LOG_MESSAGE_FORMAT))
    logger.setLevel(level)
    logger.addHandler(handler)


async def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        '-l', '--logging-level',
        default='DEBUG',
        choices=['DISABLE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    )
    parser.add_argument(
        '-p', '--print-config-schema',
        action='store_true'
    )
    parser.add_argument(
        '-v', '--version',
        action='store_true'
    )
    arguments = parser.parse_args()

    if arguments.version:
        print(VERSION)
        return

    if arguments.print_config_schema:
        schema = await get_schema()
        print(schema)
        return

    configure_logging(level=arguments.logging_level)

    app: TorrentRSS
    try:
        app = await TorrentRSS.from_path()
    except FileNotFoundError:
        parser.error(
            f'No config file found at {str(CONFIG_PATH)!r}. '
            + "See '--print-config-schema' for reference."
        )

    try:
        await app.run()
    except Exception as error:
        logger.exception(error.__class__.__name__)
        show_exception_notification(error)
        parser.exit(2)
