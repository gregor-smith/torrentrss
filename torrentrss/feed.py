from __future__ import annotations

from typing import Dict, Optional, AsyncIterator, Tuple

from aiohttp import ClientSession
from feedparser import FeedParserDict, parse as parse_feed

from .utils import Json
from .logging import logger
from .errors import FeedError
from .constants import TORRENT_MIMETYPE
from .subscription import Subscription
from .episode_number import EpisodeNumber


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

        rss = parse_feed(text)
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
