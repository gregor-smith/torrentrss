import io
import re
import json
import unittest
from unittest.mock import patch, MagicMock, ANY, call

import pytest
import feedparser

from torrentrss import (
    TorrentRSS,
    Command,
    Feed,
    Subscription,
    EpisodeNumber,
    ConfigError
)

URL = 'http://test.com/test.torrent'
NAME = 'test name'
PATTERN = r'test pattern (?P<episode>.)'
CONTENT = b'test'


class TestCommand:
    @patch('subprocess.Popen')
    def test_arguments(self, popen):
        command = Command(['command', '$URL', '--option'])
        command(URL)
        popen.assert_called_once_with(
            args=['command', URL, '--option'],
            startupinfo=ANY
        )

    @patch.object(Command, 'launch_url')
    def test_no_arguments(self, launch_url):
        command = Command()
        command(URL)
        launch_url.assert_called_once_with(URL)


class TestEpisodeNumber:
    def test_comparison(self):
        assert EpisodeNumber(None, 1) > EpisodeNumber(None, None)
        assert EpisodeNumber(None, 2) > EpisodeNumber(None, 1)
        assert EpisodeNumber(1, 1) > EpisodeNumber(None, None)
        assert not EpisodeNumber(None, None) > EpisodeNumber(1, 1)
        assert EpisodeNumber(2, 1) > EpisodeNumber(1, 2)
        assert not EpisodeNumber(1, 2) > EpisodeNumber(2, 1)

    def test_from_regex(self):
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


class TestSubscription:
    def test_properties(self):
        name = NAME
        pattern = PATTERN
        command = ['test', 'command']
        sub = Subscription(
            feed=MagicMock(),
            name=name,
            pattern=pattern,
            command=command
        )

        assert sub.name == name
        assert sub.regex.pattern == pattern
        assert sub.number.series is None
        assert sub.number.episode is None
        assert sub.command.arguments == command

    def test_uses_config_default_properties(self):
        default_command = Command()
        feed = MagicMock(**{
            'config.default_command': default_command
        })
        sub = Subscription(feed=feed, name=NAME, pattern=PATTERN)

        assert sub.command is default_command

    def test_invalid_regex(self):
        with pytest.raises(ConfigError):
            Subscription(feed=MagicMock(), name=NAME, pattern='[')
        with pytest.raises(ConfigError):
            Subscription(feed=MagicMock(), name=NAME, pattern='no group')


class TestFeed(unittest.TestCase):
    def setUp(self):
        self.config = TorrentRSS('./testconfig.json')
        self.feed = self.config.feeds['Test feed 1']
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())
        self.entry = self.rss.entries[0]

    def test_properties(self):
        assert self.feed.config is self.config
        assert self.feed.name == 'Test feed 1'
        assert self.feed.url == 'https://test.com/rss'
        assert self.feed.user_agent is None
        assert 'Test sub 1' in self.feed.subscriptions
        assert 'Test sub 2' in self.feed.subscriptions

    def test_matching_subs(self):
        with patch.object(self.feed, 'fetch', return_value=self.rss):
            matches = list(self.feed.matching_subs())

        sub1 = self.feed.subscriptions['Test sub 1']
        sub2 = self.feed.subscriptions['Test sub 2']
        assert matches == [
            (sub2, self.rss.entries[14]),
            (sub1, self.rss.entries[13]),
            (sub1, self.rss.entries[12]),
            (sub2, self.rss.entries[11]),
            (sub2, self.rss.entries[10]),
            (sub2, self.rss.entries[6]),
            (sub2, self.rss.entries[4]),
            (sub2, self.rss.entries[3]),
            (sub1, self.rss.entries[2]),
            (sub1, self.rss.entries[0]),
        ]
        assert sub1.number == sub2.number == EpisodeNumber(3, 5)

    def test_get_entry_url(self):
        result = self.feed.get_entry_url(self.entry)
        assert result == 'https://test.rss/20.torrent'


class TestTorrentRSS(unittest.TestCase):
    def setUp(self):
        self.config = TorrentRSS('./testconfig.json')
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())

    def test_properties(self):
        assert self.config.default_command.arguments is None
        assert self.config.default_user_agent is None
        assert 'Test feed 1' in self.config.feeds
        assert 'Test feed 2' in self.config.feeds

    def test_check_feeds(self):
        with patch.object(Feed, 'fetch', return_value=self.rss),  \
                patch.object(Command, '__call__') as command:
            self.config.check_feeds()

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

    def test_save_episode_numbers(self):
        with patch.object(Feed, 'fetch', return_value=self.rss),  \
                patch.object(Command, '__call__'):
            self.config.check_feeds()

        feed1 = self.config.feeds['Test feed 1'].subscriptions
        feed2 = self.config.feeds['Test feed 2'].subscriptions
        assert feed1['Test sub 1'].number == EpisodeNumber(3, 5)
        assert feed1['Test sub 2'].number == EpisodeNumber(3, 5)
        assert feed2['Sub at current episode'].number == EpisodeNumber(3, 5)
        assert feed2['Sub at greater episode'].number == EpisodeNumber(99, 99)
        assert feed2['Test sub 3'].number == EpisodeNumber(3, 5)
        assert feed2['Sub matching nothing'].number == EpisodeNumber(None, None)

        with io.StringIO() as file:
            self.config.save_episode_numbers(file)
            file.seek(io.SEEK_SET)
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
