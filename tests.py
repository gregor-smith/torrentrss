import re
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY

import pytest
import feedparser

from torrentrss import (TorrentRSS, Command, Feed, Subscription,
                        EpisodeNumber, ConfigError, FeedError)

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


class TestFeed(unittest.TestCase):
    def setUp(self):
        with open('./testconfig.json', encoding='utf-8') as file:
            self.config = TorrentRSS(file)
        self.feed = self.config.feeds['Test feed 1']
        with open('./testfeed.xml', encoding='utf-8') as file:
            self.rss = feedparser.parse(file.read())
        self.entry = self.rss.entries[0]

    def test_properties(self):
        assert self.feed.config is self.config
        assert self.feed.name == 'Test feed 1'
        assert self.feed.url == 'https://test.com/rss'
        assert self.feed.user_agent is None
        assert self.feed.enabled
        assert self.feed.prefer_torrent_url
        assert self.feed.hide_torrent_filename
        assert 'Disabled sub' in self.feed.subscriptions
        assert 'Test sub 1' in self.feed.subscriptions
        assert 'Test sub 2' in self.feed.subscriptions

    def test_matching_subs(self):
        with patch.object(self.feed, 'fetch', return_value=self.rss):
            matches = list(self.feed.matching_subs())

        sub1 = self.feed.subscriptions['Test sub 1']
        sub2 = self.feed.subscriptions['Test sub 2']
        assert matches == [
            (sub2, self.rss.entries[48], EpisodeNumber(10, 4)),
            (sub1, self.rss.entries[46], EpisodeNumber(9, 10)),
            (sub1, self.rss.entries[41], EpisodeNumber(10, 1)),
            (sub2, self.rss.entries[37], EpisodeNumber(10, 5)),
            (sub1, self.rss.entries[31], EpisodeNumber(10, 2)),
            (sub1, self.rss.entries[30], EpisodeNumber(10, 3)),
            (sub2, self.rss.entries[24], EpisodeNumber(10, 6)),
            (sub2, self.rss.entries[20], EpisodeNumber(10, 7)),
            (sub1, self.rss.entries[19], EpisodeNumber(10, 4)),
            (sub2, self.rss.entries[18], EpisodeNumber(10, 8)),
            (sub1, self.rss.entries[17], EpisodeNumber(10, 5)),
            (sub1, self.rss.entries[16], EpisodeNumber(10, 6)),
            (sub1, self.rss.entries[15], EpisodeNumber(10, 7)),
            (sub1, self.rss.entries[14], EpisodeNumber(10, 8)),
            (sub1, self.rss.entries[13], EpisodeNumber(10, 9)),
            (sub1, self.rss.entries[11], EpisodeNumber(10, 10)),
            (sub2, self.rss.entries[10], EpisodeNumber(10, 9)),
            (sub2, self.rss.entries[9], EpisodeNumber(10, 10)),
        ]
        assert sub1.number == sub2.number == EpisodeNumber(10, 10)

    def test_torrent_url_for_entry_with_link_only(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[0])
        assert result == 'https://test.rss/49.torrent'

    def test_torrent_url_for_entry_with_torrent_enclosure(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[1])
        assert result == 'https://test.rss/48.torrent'

    def test_torrent_url_for_entry_with_non_torrent_enclosure(self):
        result = self.feed.torrent_url_for_entry(self.rss.entries[2])
        assert result == 'https://test.rss/47.torrent'

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
