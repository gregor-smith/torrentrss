import pytest
from feedparser import FeedParserDict, parse

from .utils import local_path
from ..feed import Feed
from ..torrentrss import TorrentRSS
from ..utils import read_text


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
