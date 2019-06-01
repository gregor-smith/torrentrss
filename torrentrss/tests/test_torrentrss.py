import json
from io import StringIO, SEEK_SET
from unittest.mock import patch, call

import pytest
from feedparser import FeedParserDict

from ..torrentrss import TorrentRSS
from ..episode_number import EpisodeNumber
from ..feed import Feed
from ..command import Command
from .utils import task_mock


def test_properties(config: TorrentRSS) -> None:
    assert config.default_command.arguments is None
    assert 'Test feed 1' in config.feeds
    assert 'Test feed 2' in config.feeds


@pytest.mark.asyncio
async def test_check_feeds(config: TorrentRSS, rss: FeedParserDict) -> None:
    with patch.object(Feed, 'fetch', return_value=task_mock(rss)),  \
            patch.object(Command, '__call__', return_value=task_mock()) as command:
        await config.check_feeds()

    expected = [
        call('https://test.rss/6.torrent'),
        call('https://test.rss/7.torrent'),
        call('https://test.rss/8.torrent'),
        call('https://test.rss/9.torrent'),
        call('https://test.rss/10.torrent'),
        call('https://test.rss/14.torrent'),
        call('https://test.rss/16.torrent'),
        call('https://test.rss/17.torrent'),
        call('https://test.rss/18.torrent'),
        call('https://test.rss/20.torrent'),
        call('https://test.rss/16.torrent'),
        call('https://test.rss/17.torrent'),
    ]
    assert command.call_args_list == expected


@pytest.mark.asyncio
async def test_save_episode_numbers(config: TorrentRSS, rss: FeedParserDict):
    with patch.object(Feed, 'fetch', return_value=task_mock(rss)),  \
            patch.object(Command, '__call__', return_value=task_mock()):
        await config.check_feeds()

    feed1 = config.feeds['Test feed 1'].subscriptions
    feed2 = config.feeds['Test feed 2'].subscriptions
    assert feed1['Test sub 1'].number == EpisodeNumber(3, 5)
    assert feed1['Test sub 2'].number == EpisodeNumber(3, 5)
    assert feed2['Sub at current episode'].number == EpisodeNumber(3, 5)
    assert feed2['Sub at greater episode'].number == EpisodeNumber(99, 99)
    assert feed2['Test sub 3'].number == EpisodeNumber(3, 5)
    assert feed2['Sub matching nothing'].number == EpisodeNumber(None, None)

    with StringIO() as file:
        await config.save_episode_numbers(file)
        file.seek(SEEK_SET)
        json_dict = json.load(file)

    feed1 = json_dict['feeds']['Test feed 1']['subscriptions']
    feed2 = json_dict['feeds']['Test feed 2']['subscriptions']
    assert feed1['Test sub 1']['series_number'] == 3
    assert feed1['Test sub 1']['episode_number'] == 5
    assert feed1['Test sub 2']['series_number'] == 3
    assert feed1['Test sub 2']['episode_number'] == 5
    assert feed2['Sub at current episode']['series_number'] == 3
    assert feed2['Sub at current episode']['episode_number'] == 5
    assert feed2['Sub at greater episode']['series_number'] == 99
    assert feed2['Sub at greater episode']['episode_number'] == 99
    assert feed2['Test sub 3']['series_number'] == 3
    assert feed2['Test sub 3']['episode_number'] == 5
    assert 'series_number' not in feed2['Sub matching nothing']
    assert 'episode_number' not in feed2['Sub matching nothing']
