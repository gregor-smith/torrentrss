import io
import re
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY, call

import pytest
import feedparser

from torrentrss import (TorrentRSS, Command, Feed, Subscription,
                        EpisodeNumber, ConfigError, FeedError,
                        WINDOWS, TEMPORARY_DIRECTORY)

PATH = '/home/test/テスト'
NAME = 'test name'
PATTERN = r'test pattern (?P<episode>.)'
CONTENT = b'test'


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
        assert not EpisodeNumber(None, None) > EpisodeNumber(1, 1)
        assert EpisodeNumber(2, 1) > EpisodeNumber(1, 2)
        assert not EpisodeNumber(1, 2) > EpisodeNumber(2, 1)

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


class TestFeed(unittest.TestCase):
    def setUp(self):
        self.config = TorrentRSS(Path('./testconfig.json'))
        self.feed = self.config.feeds['Test feed 1']
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())
        self.entry = self.rss.entries[0]

    def test_properties(self):
        assert self.feed.config is self.config
        assert self.feed.name == 'Test feed 1'
        assert self.feed.url == 'https://test.com/rss'
        assert self.feed.user_agent is None
        assert self.feed.prefer_torrent_url
        assert self.feed.hide_torrent_filename
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

    def test_torrent_url_for_entry_with_link_only(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[0])
        assert result == 'https://test.rss/20.torrent'

    def test_torrent_url_for_entry_with_torrent_enclosure(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[1])
        assert result == 'https://test.rss/19.torrent'

    def test_torrent_url_for_entry_with_non_torrent_enclosure(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[2])
        assert result == 'https://test.rss/18.torrent'

    @patch.object(Path, 'write_bytes')
    @patch.object(Path, 'mkdir')
    @patch('requests.get', return_value=MagicMock(content=CONTENT))
    def test_download_entry_torrent_file(self, requests_get, mkdir, write_bytes):
        path = self.feed.download_entry_torrent_file(
            url=self.entry['link'], title=self.entry['title'],
            directory=Path(PATH)
        )
        requests_get.assert_called_once_with(self.entry['link'], headers={})
        mkdir.assert_called_once()
        write_bytes.assert_called_once_with(CONTENT)
        assert path == Path(
            PATH,
            '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.torrent'
        )

    @patch.object(Path, 'write_bytes')
    @patch.object(Path, 'mkdir')
    @patch('requests.get', return_value=MagicMock(content=CONTENT))
    def test_download_entry_torrent_file_custom_user_agent(self, requests_get, *args):
        self.feed.user_agent = 'test user agent'
        self.feed.download_entry_torrent_file(
            url=self.entry['link'], title=self.entry['title'],
            directory=Path(PATH)
        )
        requests_get.assert_called_once_with(
            self.entry['link'], headers={'User-Agent': 'test user agent'}
        )

    @patch.object(Path, 'write_bytes')
    @patch.object(Path, 'mkdir')
    @patch('requests.get', return_value=MagicMock(content=CONTENT))
    def test_download_entry_torrent_file_no_hide_filename(self, *args):
        self.feed.hide_torrent_filename = False
        path = self.feed.download_entry_torrent_file(
            url=self.entry['link'], title=self.entry['title'],
            directory=Path(PATH)
        )
        assert path == Path(PATH, self.entry['title'] + '.torrent')

    @patch.object(Path, 'write_bytes')
    @patch.object(Path, 'mkdir')
    @patch('requests.get', return_value=MagicMock(content=CONTENT))
    def test_download_entry_torrent_file_replace_forbidden_characters(self, *args):
        self.config.replace_windows_forbidden_characters = True
        self.feed.hide_torrent_filename = False
        path = self.feed.download_entry_torrent_file(
            url=self.entry['link'],
            title='\テスト/ :string* full? of" <forbidden> characters|',
            directory=Path(PATH)
        )
        assert path == Path(
            PATH, '_テスト_ _string_ full_ of_ _forbidden_ characters_.torrent'
        )

    def test_download_entry_url_preferred(self):
        path_or_url = self.feed.download_entry(self.entry, Path(PATH))
        assert path_or_url == self.entry['link']

    @patch.object(Feed, 'torrent_url_for_entry', side_effect=KeyError)
    def test_download_entry_url_preferred_error(self, *args):
        with pytest.raises(FeedError):
            self.feed.download_entry(self.entry, Path(PATH))

    @patch.object(Path, 'write_bytes')
    @patch.object(Path, 'mkdir')
    @patch('requests.get', return_value=MagicMock(content=CONTENT))
    def test_download_entry_no_url_preferred(self, *args):
        self.feed.prefer_torrent_url = self.feed.hide_torrent_filename = False
        path_or_url = self.feed.download_entry(self.entry, Path(PATH))
        assert path_or_url == Path(PATH, self.entry['title'] + '.torrent')

    @patch.object(Feed, 'download_entry_torrent_file', side_effect=IOError)
    def test_download_entry_no_url_preferred_error(self, *args):
        self.feed.prefer_torrent_url = False
        with pytest.raises(FeedError):
            self.feed.download_entry(self.entry, Path(PATH))


class TestTorrentRSS(unittest.TestCase):
    def setUp(self):
        self.config = TorrentRSS(Path('./testconfig.json'))
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())

    def test_properties(self):
        assert self.config.default_directory == TEMPORARY_DIRECTORY
        assert self.config.default_command.arguments is None
        assert not self.config.default_command.shell
        assert self.config.replace_windows_forbidden_characters == WINDOWS
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
