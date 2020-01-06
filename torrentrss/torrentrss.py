from __future__ import annotations

import json
from io import StringIO
from os import PathLike
from typing import Dict, Optional

import jsonschema

from . import logging
from .feed import Feed
from .command import Command
from .utils import Json, read_text, write_text
from .constants import CONFIG_PATH, CONFIG_SCHEMA


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
        jsonschema.validate(config, CONFIG_SCHEMA)

        return cls(path, config)

    async def check_feeds(self) -> None:
        urls = [
            (
                await Feed.get_entry_url(entry),
                sub.command or self.default_command
            )
            for feed in self.feeds.values()
            async for sub, entry in feed.matching_subs()
        ]
        for url, command in urls:
            await command(url)

    # Optional parameter for writing to a StringIO during testing
    async def save_episode_numbers(self, file: Optional[StringIO] = None) -> None:
        await logging.info('Writing episode numbers')

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
