from unittest.mock import MagicMock

import pytest

from ..subscription import Subscription
from ..episode_number import EpisodeNumber
from ..errors import ConfigError


def test_properties() -> None:
    sub = Subscription(
        feed=MagicMock(),
        name='test subscription',
        pattern=r'test pattern (?P<episode>.)',
        command=['test', 'command']
    )

    assert sub.name == 'test subscription'
    assert sub.regex.pattern == r'test pattern (?P<episode>.)'
    assert sub.number == EpisodeNumber(None, None)
    assert sub.command is not None
    assert sub.command.arguments == ['test', 'command']


@pytest.mark.parametrize('pattern', ('[', 'no group'))
def test_invalid_regex(pattern) -> None:
    with pytest.raises(ConfigError):
        Subscription(
            feed=MagicMock(),
            name='test subscription',
            pattern=pattern
        )
