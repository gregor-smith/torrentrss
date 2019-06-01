import pytest
from feedparser import FeedParserDict, parse

from .. import TorrentRSS, Feed, read_text
from .utils import local_path


@pytest.fixture
async def config() -> TorrentRSS:
    return await TorrentRSS.from_path(local_path('./testconfig.json'))


@pytest.fixture
def feed(config: TorrentRSS) -> Feed:
    return config.feeds['Test feed 1']


@pytest.fixture
async def rss() -> FeedParserDict:
    text = await read_text(local_path('./testfeed.xml'))
    return parse(text)
