from __future__ import annotations

import re
from typing import TYPE_CHECKING, Pattern, Optional, List

from .errors import ConfigError
from .command import Command
from .episode_number import EpisodeNumber
if TYPE_CHECKING:
    from .feed import Feed


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
