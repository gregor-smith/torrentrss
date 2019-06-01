from unittest.mock import patch

import pytest
from feedparser import FeedParserDict

from .. import Feed, EpisodeNumber
from .utils import task_mock


@pytest.mark.asyncio
async def test_properties(feed: Feed) -> None:
    assert feed.name == 'Test feed 1'
    assert feed.url == 'https://test.com/rss'
    assert feed.user_agent is None
    assert 'Test sub 1' in feed.subscriptions
    assert 'Test sub 2' in feed.subscriptions


@pytest.mark.asyncio
async def test_matching_subs(feed: Feed, rss: FeedParserDict) -> None:
    with patch.object(feed, 'fetch', return_value=task_mock(rss)):
        matches = []
        async for match in feed.matching_subs():
            matches.append(match)

    sub1 = feed.subscriptions['Test sub 1']
    sub2 = feed.subscriptions['Test sub 2']
    assert matches == [
        (sub2, rss.entries[14]),
        (sub1, rss.entries[13]),
        (sub1, rss.entries[12]),
        (sub2, rss.entries[11]),
        (sub2, rss.entries[10]),
        (sub2, rss.entries[6]),
        (sub2, rss.entries[4]),
        (sub2, rss.entries[3]),
        (sub1, rss.entries[2]),
        (sub1, rss.entries[0]),
    ]
    assert sub1.number == sub2.number == EpisodeNumber(3, 5)


@pytest.mark.asyncio
async def test_get_entry_url(rss: FeedParserDict) -> None:
    result = await Feed.get_entry_url(rss.entries[0])
    assert result == 'https://test.rss/20.torrent'
