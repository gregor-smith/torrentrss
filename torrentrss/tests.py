import io
import sys
import json
import time
import hashlib
import pathlib
import tempfile
import unittest
import collections
from unittest.mock import patch, MagicMock

from pkg_resources import parse_version

from . import _torrentrss as torrentrss

minimal_config = '''{
    "feeds": {
        "テスト feed": {
            "url": "https://test.url/テスト",
            "subscriptions": {
                "テスト sub": {
                    "pattern": "テスト filename ([0-9]+)"
                },
                "disabled sub": {
                    "pattern": "doesn't matter ([0-9]+)",
                    "enabled": false
                }
            }
        }
    }
}'''

def config_from_string(string):
    with patch('io.open', return_value=io.StringIO(string)):
        return torrentrss.Config()

class UncloseableStringIO(io.StringIO):
    def close(self):
        pass

class TestMinimalConfig(unittest.TestCase):
    # no need to test if the config passes the schema validation,
    # as that validation is done every setUp call
    def setUp(self):
        self.config = config_from_string(minimal_config)

    def test_properties(self):
        self.assertIsNone(self.config.exception_gui)
        self.assertTrue(self.config.remove_old_log_files_enabled)
        self.assertEqual(self.config.log_file_limit,
                         torrentrss.DEFAULT_LOG_FILE_LIMIT)
        self.assertIs(self.config.default_directory,
                      torrentrss.TEMPORARY_DIRECTORY)
        self.assertIsInstance(self.config.default_command,
                              torrentrss.StartFileCommand)
        self.assertIn('テスト feed', self.config)

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

    def test_setting_exception_gui_to_invalid_value(self):
        with self.assertRaises(torrentrss.ConfigError):
            self.config.exception_gui = 'test'

    @patch('shutil.which', return_value=None)
    def test_setting_exception_gui_to_notify_send_not_on_path(self, which):
        with self.assertRaises(torrentrss.ConfigError):
            self.config.exception_gui = 'notify-send'

    @patch('subprocess.Popen')
    @patch.object(torrentrss, 'LOG_DIR', pathlib.PurePosixPath('/test'))
    def test_show_notify_send_exception_gui(self, popen):
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

    def test_remove_old_log_files(self):
        self.config.log_file_limit = 2

        logs = ['dir_1/file_1.log',
                'dir_2/subdir_1/file_1.log',
                'dir_2/file_1.log',
                'dir_2/file_2.log',
                'dir_3/file_1.log']
        log_paths = collections.OrderedDict()

        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            for log in logs:
                path = log_paths[log] = directory / log
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                time.sleep(0.01)  # to guarantee differing st_ctime

            with patch.object(torrentrss, 'LOG_DIR', directory):
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
        self.config['テスト feed']['テスト sub'].number = number

        # need to override the close method else the file would be closed by
        # the with block in save_new_episode_number
        with patch('io.open', return_value=UncloseableStringIO()) as file:
            self.config.save_new_episode_numbers()
            # patch() as a context manager returns the MagicMock object,
            # which when called returns the StringIO return_value,
            # whose getvalue() method finally returns the dumped json string
            json_dict = json.loads(file().getvalue())
        return json_dict['feeds']['テスト feed']['subscriptions']['テスト sub']

    def test_save_new_episode_number(self):
        sub = self._dump_and_load_number(parse_version('S01E01'))
        self.assertEqual(sub['number'], 'S01E01')

    def test_none_episode_number_not_s(self):
        sub = self._dump_and_load_number(None)
        self.assertNotIn('number', sub)

@patch.object(pathlib.Path, 'write_bytes')
@patch.object(pathlib.Path, 'mkdir')
@patch('requests.get', return_value=MagicMock(content=b''))
class TestMinimalConfigFeed(unittest.TestCase):
    directory = pathlib.Path('テスト')
    entry_filename = 'テスト filename 01'
    entry_main_link = 'https://test.url/テスト/is-the-main-link'
    entry_torrent_link = 'https://test.url/テスト/is-a-torrent-link'
    entry_magnet_link = 'magnet:?is-a-magnet-link'

    def setUp(self):
        self.config = config_from_string(minimal_config)
        self.feed = self.config['テスト feed']
        self.entry = {
            'title': self.entry_filename,
            'link': self.entry_main_link,
            'links': [{'href': 'https://test.url/テスト/not-a-torrent-link',
                       'type': 'text/html'},
                      {'href': self.entry_torrent_link,
                       'type': 'application/x-bittorrent'},
                      {'href': 'https://test.url/テスト/not-a-torrent-link2',
                       'type': 'text/html'}],
            'torrent_magneturi': self.entry_magnet_link
        }
        self.rss = {'encoding': 'utf-8',
                    'entries': [{'title': 'mismatch filename'},
                                self.entry,
                                {'title': 'mismatch filename 2'}]}

    def test_windows_forbidden_characters_regex(self, *args):
        original = '\テスト/ :string* full? of" <forbidden> characters|'
        desired = '-テスト- -string- full- of- -forbidden- characters-'
        result = self.feed.windows_forbidden_characters_regex.sub('-',
                                                                  original)
        self.assertEqual(result, desired)

    def test_properties(self, *args):
        self.assertEqual(self.feed.name, 'テスト feed')
        self.assertEqual(self.feed.url, 'https://test.url/テスト')
        self.assertIn('テスト sub', self.feed)
        self.assertIsNone(self.feed.user_agent)
        self.assertTrue(self.feed.enabled)
        self.assertTrue(self.feed.magnet_enabled)
        self.assertTrue(self.feed.torrent_url_enabled)
        self.assertTrue(self.feed.torrent_file_enabled)
        self.assertTrue(self.feed.hide_torrent_filename_enabled)

    def test_torrent_url_for_entry(self, *args):
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry_torrent_link)

    def test_torrent_url_for_entry_no_torrent_mimetype_link(self, *args):
        self.entry['links'][1]['type'] = 'application/x-not-bittorrent'
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry_main_link)

    def test_magnet_uri_for_entry(self, *args):
        self.assertEqual(self.feed.magnet_uri_for_entry(self.entry),
                         self.entry_magnet_link)

    def test_magnet_uri_for_entry_when_none_exists(self, *args):
        del self.entry['torrent_magneturi']
        with self.assertRaises(KeyError):
            self.feed.magnet_uri_for_entry(self.entry)

    def _run_download_entry_torrent_file(self, *args):
        return self.feed.download_entry_torrent_file(url=None,
                                                     rss_entry=self.entry,
                                                     directory=self.directory)

    def test_download_entry_torrent_file_none_user_agent(self,
                                                         requests_get_mock,
                                                         *args):
        self._run_download_entry_torrent_file()
        requests_get_mock.assert_called_once_with(None, headers={})

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
        desired_path = self._torrent_path(self.entry_filename)
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

    def test_matching_subs(self, *args):
        with patch.object(self.feed, 'fetch', return_value=self.rss):
            subs = self.feed.matching_subs()
            sub, entry, number = next(subs)
        self.assertIs(entry, self.entry)
        self.assertEqual(number, parse_version('01'))
        with self.assertRaises(StopIteration):
            next(subs)

    def test_enabled_subs(self, *args):
        self.assertNotIn(self.feed['disabled sub'],
                         self.feed.enabled_subs())

class TestSubscription(unittest.TestCase):
    default_directory = torrentrss.TEMPORARY_DIRECTORY
    default_command = torrentrss.StartFileCommand()

    def _mock_feed(self):
        feed = MagicMock()
        feed.config.default_directory = self.default_directory
        feed.config.default_command = self.default_command
        return feed

    def _minimal_sub(self, feed=None, name='', pattern='()', **kwargs):
        return torrentrss.Subscription(feed=feed or self._mock_feed(),
                                       name=name, pattern=pattern, **kwargs)

    def test_minimal_properties(self):
        name = 'テスト sub'
        pattern = r'テスト filename ([0-9]+)'
        sub = self._minimal_sub(name=name, pattern=pattern)

        self.assertEqual(sub.name, name)
        self.assertEqual(sub.regex.pattern, pattern)
        self.assertIsNone(sub.number)
        self.assertIs(sub.directory, self.default_directory)
        self.assertIs(sub.command, self.default_command)
        self.assertTrue(sub.enabled)

    def test_properties(self):
        command_arguments = ['test', 'command']
        directory = '/home/test/テスト'
        sub = self._minimal_sub(directory=directory, command=command_arguments)
        self.assertIsInstance(sub.command, torrentrss.Command)
        self.assertEqual(sub.command.arguments, command_arguments)
        self.assertIsInstance(sub.directory, pathlib.Path)
        self.assertEqual(sub.directory.as_posix(), directory)

    def test_invalid_regex_raises_configerror(self):
        with self.assertRaises(torrentrss.ConfigError):
            self._minimal_sub(pattern='[')

    def test_regex_without_group_raises_configerror(self):
        with self.assertRaises(torrentrss.ConfigError):
            self._minimal_sub(pattern='no group in sight')

    def test_has_lower_number_than(self):
        sub = self._minimal_sub(number='00')
        self.assertFalse(sub.has_lower_number_than(None))
        self.assertTrue(sub.has_lower_number_than(parse_version('01')))

        sub.number = parse_version('S01E01')
        self.assertTrue(sub.has_lower_number_than(parse_version('S01E02')))
        self.assertTrue(sub.has_lower_number_than(parse_version('S02E01')))

        sub.number = None
        self.assertFalse(sub.has_lower_number_than(None))
        self.assertTrue(sub.has_lower_number_than(parse_version('01')))
        self.assertTrue(sub.has_lower_number_than(parse_version('S01E01')))

class TestCommand(unittest.TestCase):
    def test_path_substitution(self):
        command = torrentrss.Command(['command', '$PATH_OR_URL', '--option'])
        path = '/home/test/テスト'
        self.assertEqual(list(command.arguments_with_substituted_path(path)),
                         ['command', path, '--option'])

if __name__ == '__main__':
    unittest.main()
