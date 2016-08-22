import io
import os
import re
import sys
import json
import time
import hashlib
import tempfile
import unittest
import collections
from pathlib import Path, PurePosixPath
from unittest.mock import patch, MagicMock, call, ANY

import torrentrss

# TODO
# configure_logging() tests
# main() tests

class TestCommand(unittest.TestCase):
    def setUp(self):
        self.path = '/home/test/テスト'

    @patch('subprocess.Popen')
    def test_command_with_arguments(self, popen):
        command = torrentrss.Command(['command', '$PATH_OR_URL', '--option'],
                                     shell=True)
        command(self.path)
        popen.assert_called_once_with(['command', self.path, '--option'],
                                      shell=True, startupinfo=ANY)

    @patch.object(torrentrss.Command, 'startfile')
    def test_command_with_no_arguments(self, startfile):
        command = torrentrss.Command()
        command(self.path)
        startfile.assert_called_once_with(self.path)

class TestEpisodeNumber(unittest.TestCase):
    def test_comparison(self):
        self.assertGreater(torrentrss.EpisodeNumber(None, 1),
                           torrentrss.EpisodeNumber(None, None))
        self.assertGreater(torrentrss.EpisodeNumber(None, 2),
                           torrentrss.EpisodeNumber(None, 1))
        self.assertGreater(torrentrss.EpisodeNumber(1, 1),
                           torrentrss.EpisodeNumber(None, None))
        self.assertGreater(torrentrss.EpisodeNumber(2, 1),
                           torrentrss.EpisodeNumber(1, 2))

    def test_from_regex_match(self):
        match = re.search(r'S(?P<series>[0-9]{2})E(?P<episode>[0-9]{2})',
                          'S01E01')
        self.assertEqual(torrentrss.EpisodeNumber(1, 1),
                         torrentrss.EpisodeNumber.from_regex_match(match))
        match = re.search(r'S[0-9]{2}E(?P<episode>[0-9]{2})', 'S01E01')
        self.assertEqual(torrentrss.EpisodeNumber(None, 1),
                         torrentrss.EpisodeNumber.from_regex_match(match))

class TestSubscription(unittest.TestCase):
    default_directory = torrentrss.TEMPORARY_DIRECTORY
    default_command = torrentrss.Command()

    def _mock_feed(self):
        feed = MagicMock()
        feed.config.default_directory = self.default_directory
        feed.config.default_command = self.default_command
        return feed

    def _minimal_sub(self, feed=None, name='',
                     pattern=r'(?P<episode>)', **kwargs):
        return torrentrss.Subscription(feed=feed or self._mock_feed(),
                                       name=name, pattern=pattern, **kwargs)

    def test_minimal_properties(self):
        name = 'テスト sub'
        pattern = r'テスト filename (?P<episode>[0-9]+)'
        sub = self._minimal_sub(name=name, pattern=pattern)

        self.assertEqual(sub.name, name)
        self.assertEqual(sub.regex.pattern, pattern)
        self.assertIsNone(sub.number.series)
        self.assertIsNone(sub.number.episode)
        self.assertIs(sub.directory, self.default_directory)
        self.assertIs(sub.command, self.default_command)
        self.assertTrue(sub.enabled)

    def test_properties(self):
        directory = '/home/test/テスト'
        sub = self._minimal_sub(directory=directory)
        self.assertIsInstance(sub.directory, Path)
        self.assertEqual(sub.directory.as_posix(), directory)

    def test_invalid_regex_raises_configerror(self):
        with self.assertRaises(torrentrss.ConfigError):
            self._minimal_sub(pattern='[')

    def test_regex_without_group_raises_configerror(self):
        with self.assertRaises(torrentrss.ConfigError):
            self._minimal_sub(pattern='no group in sight')

config_string = '''{
    "feeds": {
        "テスト feed 1": {
            "url": "https://test.url/テスト1",
            "subscriptions": {
                "テスト sub 1": {
                    "pattern": "テスト file feed 1 sub 1 ep (?P<episode>[0-9]+)"
                },
                "disabled sub": {
                    "pattern": "doesn't matter (?P<episode>[0-9]+)",
                    "enabled": false
                },
                "テスト sub 2": {
                    "pattern": "テスト file feed 1 sub 2 ep (?P<episode>[0-9]+)",
                    "episode_number": 1
                },
                "テスト sub 3": {
                    "pattern": "テスト file feed 1 sub 3 ep (?P<episode>[0-9]+)",
                    "episode_number": 1
                }
            }
        },
        "テスト feed 2": {
            "url": "https://test.url/テスト2",
            "subscriptions": {
                "テスト sub 1": {
                    "pattern": "テスト file feed 2 sub 1 ep S(?P<series>[0-9]+)E(?P<episode>[0-9]+)",
                    "series_number": 1,
                    "episode_number": 1
                },
                "テスト sub 2": {
                    "pattern": "テスト file feed 2 sub 2 ep S(?P<series>[0-9]+)E(?P<episode>[0-9]+)",
                    "series_number": 99,
                    "episode_number": 99
                }
            }
        },
        "disabled feed": {
            "url": "doesn't matter",
            "subscriptions": {},
            "enabled": false
        },
        "enabled feed without subs": {
            "url": "doesn't matter",
            "subscriptions": {}
        }
    }
}'''

def config_from_string():
    with patch('io.open', return_value=io.StringIO(config_string)):
        return torrentrss.Config()

def title(**kwargs):
    return ('テスト file feed {feed} sub {sub} ep {ep}'
            .format_map(kwargs))

def fallback_link(**kwargs):
    return ('https://test.url/テスト/feed-{feed}-sub-{sub}-ep-{ep}-fallback'
            .format_map(kwargs))

def torrent_link(**kwargs):
    return ('https://test.url/テスト/feed-{feed}-sub-{sub}-ep-{ep}-torrent'
            .format_map(kwargs))

def magnet_link(**kwargs):
    return ('magnet:?テスト-feed-{feed}-sub-{sub}-ep-{ep}-magnet'
            .format_map(kwargs))

def non_torrent_entry_link():
    return {'href': 'https://test.url/テスト/not-a-torrent-link',
            'type': 'text/html'}

def entry_dict(fallback_is_torrent=False, magnet=True, **kwargs):
    entry = {'title': title(**kwargs)}
    entry['links'] = links = [non_torrent_entry_link(),
                              non_torrent_entry_link()]

    torrent = torrent_link(**kwargs)
    if fallback_is_torrent:
        entry['link'] = torrent
    else:
        links.insert(1, {'href': torrent,
                         'type': 'application/x-bittorrent'})
        entry['link'] = fallback_link(**kwargs)
    if magnet:
        entry['torrent_magneturi'] = magnet_link(**kwargs)

    return entry

rss_feeds = {
    'テスト feed 1': {'entries': [{'title': 'mismatch filename'},
                               entry_dict(feed=1, sub=1, ep=2),
                               {'title': 'mismatch filename 2'},
                               entry_dict(feed=1, sub=2, ep=2),
                               entry_dict(feed=1, sub=2, ep=1),
                               entry_dict(feed=1, sub=1, ep=3),
                               entry_dict(feed=1, sub=1, ep=1, magnet=False,
                                          fallback_is_torrent=True)]},
    'テスト feed 2': {'entries': [{'title': 'mismatch filename'},
                               entry_dict(feed=2, sub=1, ep='S01E02',
                                          magnet=False),
                               entry_dict(feed=2, sub=2, ep='S99E98')]}
}

@patch.object(Path, 'write_bytes')
@patch.object(Path, 'mkdir')
@patch('requests.get', return_value=MagicMock(content=b''))
class TestFeed(unittest.TestCase):
    directory = Path('テスト')
    entry_title = title(feed=1, sub=1, ep=1)
    entry_fallback_link = fallback_link(feed=1, sub=1, ep=1)
    entry_torrent_link = torrent_link(feed=1, sub=1, ep=1)
    entry_magnet_link = magnet_link(feed=1, sub=1, ep=1)

    def setUp(self):
        self.entry = entry_dict(feed=1, sub=1, ep=1)
        self.config = config_from_string()
        self.feed = self.config['テスト feed 1']

    def test_properties(self, *args):
        self.assertEqual(self.feed.name, 'テスト feed 1')
        self.assertEqual(self.feed.url, 'https://test.url/テスト1')
        self.assertIn('テスト sub 1', self.feed)
        self.assertIn('disabled sub', self.feed)
        self.assertIn('テスト sub 2', self.feed)
        self.assertIsNone(self.feed.user_agent)
        self.assertTrue(self.feed.enabled)
        self.assertTrue(self.feed.magnet_enabled)
        self.assertTrue(self.feed.torrent_url_enabled)
        self.assertTrue(self.feed.torrent_file_enabled)
        self.assertTrue(self.feed.hide_torrent_filename_enabled)

    def test_windows_forbidden_characters_regex(self, *args):
        original = '\テスト/ :string* full? of" <forbidden> characters|'
        desired = '-テスト- -string- full- of- -forbidden- characters-'
        result = self.feed.windows_forbidden_characters_regex.sub('-',
                                                                  original)
        self.assertEqual(result, desired)

    def test_enabled_subs(self, *args):
        self.assertNotIn(self.feed['disabled sub'],
                         self.feed.enabled_subs())

    def test_matching_subs(self, *args):
        with patch.object(self.feed, 'fetch',
                          return_value=rss_feeds['テスト feed 1']):
            matches = list(self.feed.matching_subs())
        self.assertTrue(matches)

        sub1 = self.feed['テスト sub 1']
        sub2 = self.feed['テスト sub 2']

        expected_subs_and_numbers = [(sub1, torrentrss.EpisodeNumber(None, 2)),
                                     (sub1, torrentrss.EpisodeNumber(None, 3)),
                                     (sub1, torrentrss.EpisodeNumber(None, 1)),
                                     (sub2, torrentrss.EpisodeNumber(None, 2))]
        for index, triple in enumerate(matches):
            sub, entry, number = triple
            expected_sub, expected_number = expected_subs_and_numbers[index]
            self.assertEqual(sub, expected_sub)
            self.assertEqual(number, expected_number)

    def test_torrent_url_for_entry(self, *args):
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry_torrent_link)

    def test_torrent_url_for_entry_no_torrent_mimetype_link(self, *args):
        self.entry['links'][1]['type'] = 'application/x-not-bittorrent'
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry_fallback_link)

    def test_magnet_uri_for_entry(self, *args):
        self.assertEqual(self.feed.magnet_uri_for_entry(self.entry),
                         self.entry_magnet_link)

    def test_magnet_uri_for_entry_when_none_exists(self, *args):
        del self.entry['torrent_magneturi']
        with self.assertRaises(KeyError):
            self.feed.magnet_uri_for_entry(self.entry)

    def _run_download_entry_torrent_file(self, *args):
        return self.feed.download_entry_torrent_file(
            url=None, rss_entry=self.entry, directory=self.directory
        )

    def test_download_entry_torrent_file_none_user_agent(self, requests_get,
                                                         *args):
        self._run_download_entry_torrent_file()
        requests_get.assert_called_once_with(None, headers={})

    def test_download_entry_torrent_file_with_user_agent(self, requests_get,
                                                         *args):
        self.feed.user_agent = user_agent = 'テスト user agent'
        self._run_download_entry_torrent_file()
        requests_get.assert_called_once_with(
            None, headers={'User-Agent': user_agent}
        )

    def _torrent_path(self, filename):
        return self.directory.joinpath(filename) \
            .with_suffix('.torrent') \
            .as_posix()

    def test_download_entry_torrent_file(self, *args):
        sha256 = hashlib.sha256(b'').hexdigest()
        desired_path = self._torrent_path(sha256)
        path = self._run_download_entry_torrent_file()
        self.assertEqual(path.as_posix(), desired_path)

    def test_download_entry_torrent_file_no_hidden_filename(self, *args):
        self.feed.hide_torrent_filename_enabled = False
        desired_path = self._torrent_path(self.entry_title)
        path = self._run_download_entry_torrent_file()
        self.assertEqual(path.as_posix(), desired_path)

    def _run_download_entry(self):
        return self.feed.download_entry(self.entry, self.directory)

    def test_download_entry_magnet_enabled(self, *args):
        uri = self._run_download_entry()
        self.assertEqual(uri, self.entry_magnet_link)

    def test_download_entry_magnet_disabled_url_enabled(self, *args):
        self.feed.magnet_enabled = False
        url = self._run_download_entry()
        self.assertEqual(url, self.entry_torrent_link)

    def test_download_entry_magnet_and_url_disabled_file_enabled(self, *args):
        self.feed.magnet_enabled = self.feed.torrent_url_enabled = False
        with patch.object(self.feed, 'download_entry_torrent_file') as mock:
            self._run_download_entry()
            mock.assert_called_once_with(self.entry_torrent_link,
                                         self.entry, self.directory)

    def test_download_entry_magnet_url_file_all_disabled(self, *args):
        self.feed.magnet_enabled = self.feed.torrent_url_enabled = \
            self.feed.torrent_file_enabled = False
        with self.assertRaises(torrentrss.FeedError):
            self._run_download_entry()

class UncloseableStringIO(io.StringIO):
    def close(self):
        pass

class TestConfig(unittest.TestCase):
    # no need to test if the config passes the schema validation,
    # as that validation is done every setUp call
    def setUp(self):
        self.config = config_from_string()

    def test_properties(self):
        self.assertIsNone(self.config.exception_gui)
        self.assertTrue(self.config.remove_old_log_files_enabled)
        self.assertEqual(self.config.log_file_limit,
                         torrentrss.DEFAULT_LOG_FILE_LIMIT)
        self.assertIs(self.config.default_directory,
                      torrentrss.TEMPORARY_DIRECTORY)
        self.assertIn('テスト feed 1', self.config)
        self.assertIn('テスト feed 2', self.config)
        self.assertIn('disabled feed', self.config)
        self.assertIn('enabled feed without subs', self.config)

    def test_setting_exception_gui_to_invalid_value(self):
        with self.assertRaises(torrentrss.ConfigError):
            self.config.exception_gui = 'test'

    @patch('shutil.which', return_value=None)
    def test_setting_exception_gui_to_notify_send_not_on_path(self, which):
        with self.assertRaises(torrentrss.ConfigError):
            self.config.exception_gui = 'notify-send'

    @patch('subprocess.Popen')
    @patch.object(Path, 'as_uri', return_value='file:///test')
    def test_show_notify_send_exception_gui(self, as_uri, popen):
        sys.last_type = Exception
        expected_text = ('An exception of type Exception occurred. '
                         '<a href="file:///test">Click to open the '
                         'log directory.</a>')
        expected_args = ['notify-send', '--app-name', 'torrentrss',
                         'torrentrss', expected_text]
        self.config.show_notify_send_exception_gui()
        popen.assert_called_once_with(expected_args)

    @patch('easygui.exceptionbox')
    def test_show_easygui_exception_gui(self, easygui):
        sys.last_type = Exception
        expected_text = 'An exception of type Exception occurred.'
        self.config.show_easygui_exception_gui()
        easygui.assert_called_once_with(msg=expected_text, title='torrentrss')

    @patch.object(torrentrss.Config, 'show_notify_send_exception_gui')
    @patch.object(torrentrss.Config, 'show_easygui_exception_gui')
    def _run_exceptions_shown_as_gui(self, *args, gui=None):
        # bypassing the property to avoid error when notify-send not on path
        self.config._exception_gui = gui
        with self.assertRaises(Exception), \
             self.config.exceptions_shown_as_gui():
            raise Exception
        return args

    def test_exceptions_shown_as_gui_none(self):
        easygui, notify = self._run_exceptions_shown_as_gui()
        easygui.assert_not_called()
        notify.assert_not_called()

    def test_exceptions_shown_as_gui_easygui(self):
        easygui, notify = self._run_exceptions_shown_as_gui(gui='easygui')
        easygui.assert_called_once_with()
        notify.assert_not_called()

    def test_exceptions_shown_as_gui_notify_send(self):
        easygui, notify = self._run_exceptions_shown_as_gui(gui='notify-send')
        easygui.assert_not_called()
        notify.assert_called_once_with()

    @patch.object(torrentrss.Config, 'exceptions_shown_as_gui')
    def test_check_feeds(self, *args):
        self.config.torrent_file_enabled = False

        feed_1 = self.config['テスト feed 1']
        feed_2 = self.config['テスト feed 2']

        self.assertIsNone(feed_1['テスト sub 1'].number.episode)
        self.assertEqual(feed_1['テスト sub 2'].number,
                         torrentrss.EpisodeNumber(None, 1))
        self.assertEqual(feed_1['テスト sub 3'].number,
                         torrentrss.EpisodeNumber(None, 1))
        self.assertEqual(feed_2['テスト sub 1'].number,
                         torrentrss.EpisodeNumber(1, 1))
        self.assertEqual(feed_2['テスト sub 2'].number,
                         torrentrss.EpisodeNumber(99, 99))

        with patch.object(torrentrss.Command, '__call__') as start, \
             patch.object(feed_1, 'fetch',
                          return_value=rss_feeds[feed_1.name]), \
             patch.object(feed_2, 'fetch',
                          return_value=rss_feeds[feed_2.name]):
            self.config.check_feeds()

        expected = [call(magnet_link(feed=1, sub=1, ep=2)),
                    call(magnet_link(feed=1, sub=1, ep=3)),
                    call(torrent_link(feed=1, sub=1, ep=1)),
                    call(magnet_link(feed=1, sub=2, ep=2)),
                    call(torrent_link(feed=2, sub=1, ep='S01E02'))]
        self.assertEqual(start.call_args_list, expected)

        self.assertEqual(feed_1['テスト sub 1'].number,
                         torrentrss.EpisodeNumber(None, 3))
        self.assertEqual(feed_1['テスト sub 2'].number,
                         torrentrss.EpisodeNumber(None, 2))
        self.assertEqual(feed_1['テスト sub 3'].number,
                         torrentrss.EpisodeNumber(None, 1))
        self.assertEqual(feed_2['テスト sub 1'].number,
                         torrentrss.EpisodeNumber(1, 2))
        self.assertEqual(feed_2['テスト sub 2'].number,
                         torrentrss.EpisodeNumber(99, 99))

    def test_remove_old_log_files(self):
        self.config.log_file_limit = 2

        logs = ['dir_1/file_1.log',
                'dir_2/subdir_1/file_1.log',
                'dir_2/file_1.log',
                'dir_2/file_2.log',
                'dir_3/file_1.log']
        log_paths = collections.OrderedDict()

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for log in logs:
                path = log_paths[log] = directory.joinpath(log)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                time.sleep(0.01)  # to guarantee differing st_ctime

            with patch('os.walk', return_value=list(os.walk(str(directory)))):
                self.assertEqual(self.config.log_paths_by_newest_first(),
                                 list(reversed(log_paths.values())))
                self.config.remove_old_log_files()


            self.assertFalse(log_paths[logs[0]].exists())
            self.assertFalse(log_paths[logs[1]].exists())
            self.assertFalse(log_paths[logs[1]].parent.exists())
            self.assertFalse(log_paths[logs[2]].exists())
            self.assertTrue(log_paths[logs[3]].exists())
            self.assertTrue(log_paths[logs[4]].exists())

    def _dump_and_load_number(self, number):
        self.config['テスト feed 1']['テスト sub 1'].number = number

        # need to override the close method else the file would be closed by
        # the with block in save_new_episode_number
        with patch('io.open', return_value=UncloseableStringIO()) as file:
            self.config.save_new_episode_numbers()
            # patch() as a context manager returns the MagicMock object,
            # which when called returns the StringIO return_value,
            # whose getvalue() method finally returns the dumped json string
            json_dict = json.loads(file().getvalue())
        return json_dict['feeds']['テスト feed 1']['subscriptions']['テスト sub 1']

    def test_save_new_episode_number(self):
        num = torrentrss.EpisodeNumber(1, 1)
        sub_dict = self._dump_and_load_number(num)
        new_num = torrentrss.EpisodeNumber(sub_dict['series_number'],
                                           sub_dict['episode_number'])
        self.assertEqual(num, new_num)

    def test_save_new_episode_number_none_not_saved(self):
        sub = self._dump_and_load_number(torrentrss.EpisodeNumber(None, None))
        self.assertNotIn('series_number', sub)
        self.assertNotIn('episode_number', sub)

if __name__ == '__main__':
    unittest.main()
