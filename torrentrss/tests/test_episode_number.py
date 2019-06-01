import re

import pytest

from ..episode_number import EpisodeNumber


def test_comparison() -> None:
    assert EpisodeNumber(None, 1) > EpisodeNumber(None, None)
    assert EpisodeNumber(None, 2) > EpisodeNumber(None, 1)
    assert EpisodeNumber(1, 1) > EpisodeNumber(None, None)
    assert not EpisodeNumber(None, None) > EpisodeNumber(1, 1)
    assert EpisodeNumber(2, 1) > EpisodeNumber(1, 2)
    assert not EpisodeNumber(1, 2) > EpisodeNumber(2, 1)


def test_from_regex() -> None:
    match = re.search(
        r'S(?P<series>[0-9]{2})E(?P<episode>[0-9]{2})',
        'S01E01'
    )
    assert EpisodeNumber(1, 1) == EpisodeNumber.from_regex_match(match)

    match = re.search(r'S[0-9]{2}E(?P<episode>[0-9]{2})', 'S01E01')
    assert EpisodeNumber(None, 1) == EpisodeNumber.from_regex_match(match)

    with pytest.raises(KeyError):
        match = re.search(r'S[0-9]{2}E([0-9]{2})', 'S01E01')
        EpisodeNumber.from_regex_match(match)
