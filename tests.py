import io
import re
import json
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY

import pytest
import feedparser

from torrentrss import (TorrentRSS, Command, Feed, Subscription, EpisodeNumber,
                        TEMPORARY_DIRECTORY, ConfigError, FeedError)

PATH = '/home/test/テスト'
NAME = 'test name'
PATTERN = r'test pattern (?P<episode>.)'


class TestCommand:
    @patch('subprocess.Popen')
    def test_arguments(self, popen):
        command = Command(['command', '$PATH_OR_URL', '--option'], shell=True)
        command(PATH)
        popen.assert_called_once_with(['command', PATH, '--option'],
                                      shell=True, startupinfo=ANY)

    @patch.object(Command, 'startfile')
    def test_no_arguments(self, startfile):
        command = Command()
        command(PATH)
        startfile.assert_called_once_with(PATH)


class TestEpisodeNumber:
    def test_comparison(self):
        assert EpisodeNumber(None, 1) > EpisodeNumber(None, None)
        assert EpisodeNumber(None, 2) > EpisodeNumber(None, 1)
        assert EpisodeNumber(1, 1) > EpisodeNumber(None, None)
        assert EpisodeNumber(2, 1) > EpisodeNumber(1, 2)

    def test_from_regex(self):
        match = re.search(
            r'S(?P<series>[0-9]{2})E(?P<episode>[0-9]{2})', 'S01E01'
        )
        assert EpisodeNumber(1, 1) == EpisodeNumber.from_regex_match(match)

        match = re.search(r'S[0-9]{2}E(?P<episode>[0-9]{2})', 'S01E01')
        assert EpisodeNumber(None, 1) == EpisodeNumber.from_regex_match(match)

        with pytest.raises(KeyError):
            match = re.search(r'S[0-9]{2}E([0-9]{2})', 'S01E01')
            EpisodeNumber.from_regex_match(match)


class TestSubscription:
    def test_properties(self):
        name = NAME
        pattern = PATTERN
        directory = PATH
        command = ['test', 'command']
        sub = Subscription(feed=MagicMock(), name=name, pattern=pattern,
                           directory=directory, command=command)

        assert sub.name == name
        assert sub.regex.pattern == pattern
        assert sub.number.series is None
        assert sub.number.episode is None
        assert sub.directory == Path(directory)
        assert sub.command.arguments == command
        assert not sub.command.shell
        assert sub.enabled

    def test_uses_config_default_properties(self):
        default_directory = Path(PATH)
        default_command = Command()
        feed = MagicMock(**{'config.default_directory': default_directory,
                            'config.default_command': default_command})
        sub = Subscription(feed=feed, name=NAME, pattern=PATTERN)

        assert sub.directory is default_directory
        assert sub.command is default_command

    def test_invalid_regex(self):
        with pytest.raises(ConfigError):
            Subscription(feed=MagicMock(), name=NAME, pattern='[')
        with pytest.raises(ConfigError):
            Subscription(feed=MagicMock(), name=NAME, pattern='no group')


# @patch.object(Path, 'write_bytes')
# @patch.object(Path, 'mkdir')
# @patch('requests.get', return_value=MagicMock(content=b''))
class TestFeed(unittest.TestCase):
    def setUp(self):
        with open('./testconfig.json', encoding='utf-8') as file:
            self.config = TorrentRSS(file)
        self.feed = self.config.feeds['Test feed 1']
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())

    def test_properties(self):
        assert self.feed.config is self.config
        assert self.feed.name == 'Test feed 1'
        assert self.feed.url == 'https://test.com/rss'
        assert self.feed.user_agent is None
        assert self.feed.enabled
        assert self.feed.use_magnet
        assert self.feed.use_torrent_url
        assert self.feed.use_torrent_file
        assert self.feed.hide_torrent_filename
        assert 'Disabled sub' in self.feed.subscriptions
        assert 'Test sub 1' in self.feed.subscriptions
        assert 'Test sub 2' in self.feed.subscriptions

    def test_subtitute_windows_forbidden_characters(self):
        result = Feed.substitute_windows_forbidden_characters(
            '\テスト/ :string* full? of" <forbidden> characters|'
        )
        assert result == '_テスト_ _string_ full_ of_ _forbidden_ characters_'

    def test_matching_subs(self):
        with patch.object(self.feed, 'fetch', return_value=self.rss):
            matches = list(self.feed.matching_subs())
        assert matches

        sub1 = self.feed.subscriptions['Test sub 1']
        sub2 = self.feed.subscriptions['Test sub 2']
        expected = [
            (sub2, EpisodeNumber(10, 4)),
            (sub1, EpisodeNumber(9, 10)),
            (sub1, EpisodeNumber(10, 1)),
            (sub2, EpisodeNumber(10, 5)),
            (sub1, EpisodeNumber(10, 2)),
            (sub1, EpisodeNumber(10, 3)),
            (sub2, EpisodeNumber(10, 6)),
            (sub2, EpisodeNumber(10, 7)),
            (sub1, EpisodeNumber(10, 4)),
            (sub2, EpisodeNumber(10, 8)),
            (sub1, EpisodeNumber(10, 5)),
            (sub1, EpisodeNumber(10, 6)),
            (sub1, EpisodeNumber(10, 7)),
            (sub1, EpisodeNumber(10, 8)),
            (sub1, EpisodeNumber(10, 9)),
            (sub1, EpisodeNumber(10, 10)),
            (sub2, EpisodeNumber(10, 9)),
            (sub2, EpisodeNumber(10, 10)),
        ]
        assert len(matches) == len(expected)

        for match, expect in zip(matches, expected):
            sub, entry, number = match
            expected_sub, expected_number = expect
            assert sub == expected_sub
            assert number == expected_number
