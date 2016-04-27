import io
import json
import time
import hashlib
import inspect
import pathlib
import tempfile
import unittest
import collections
from unittest.mock import patch, MagicMock, ANY

from pkg_resources import parse_version

from . import _torrentrss as torrentrss

class _TestConfig(unittest.TestCase):
    @classmethod
    def config_from_string(cls):
        with patch('io.open', return_value=io.StringIO(cls.config_string)):
            return torrentrss.Config()

    # no need to test if the config passes the schema validation,
    # as that validation is done every setUp call
    def setUp(self):
        self.config = self.config_from_string()

class UncloseableStringIO(io.StringIO):
    def close(self):
        pass

class TestMinimalConfig(_TestConfig):
    config_string = inspect.cleandoc('''{
        "feeds": {
            "テスト feed": {
                "url": "https://test.url/テスト",
                "subscriptions": {
                    "テスト sub": {
                        "pattern": "テスト filename ([0-9]+)"
                    }
                }
            }
        }
    }''')

    def test_properties(self):
        self.assertIsNone(self.config.exception_gui)
        self.assertTrue(self.config.remove_old_log_files_enabled)
        self.assertEqual(self.config.log_file_limit,
                         torrentrss.DEFAULT_LOG_FILE_LIMIT)
        self.assertIs(self.config.default_directory,
                      torrentrss.TEMPORARY_DIRECTORY)
        self.assertIsInstance(self.config.default_command,
                              torrentrss.StartFileCommand)
        self.assertIn('テスト feed', self.config.feeds)

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
        self.config.feeds['テスト feed'].subscriptions['テスト sub'].number = number

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

    def test_save_none_episode_number(self):
        sub = self._dump_and_load_number(None)
        self.assertNotIn('number', sub)

class TestMinimalConfigFeed(unittest.TestCase):
    # downloading torrent files
    # downloading when magnet, url and torrent are all disabled
    # windows forbidden characters

    def setUp(self):
        self.config = TestMinimalConfig.config_from_string()
        self.feed = self.config.feeds['テスト feed']
        self.rss = {
            'encoding': 'utf-8',
            'entries': [
                {
                    'title': 'テスト filename 1',
                    'link': 'https://test.url/テスト/is-the-main-link',
                    'links': [
                        {
                            'href': 'https://test.url/テスト/not-a-torrent-link',
                            'type': 'text/html'
                        },
                        {
                            'href': 'https://test.url/テスト/is-a-torrent-link',
                            'type': 'application/x-bittorrent'
                        },
                        {
                            'href': 'https://test.url/テスト/not-a-torrent-link2',
                            'type': 'text/html'
                        }
                    ],
                    'torrent_magneturi': 'magnet:?is-a-magnet-link'
                }
            ]
        }
        self.entry = self.rss['entries'][0]

    def test_windows_forbidden_characters_regex(self):
        original = '\テスト/ :string* full? of" <forbidden> characters|'
        desired = '-テスト- -string- full- of- -forbidden- characters-'
        result = self.feed.windows_forbidden_characters_regex.sub('-',
                                                                  original)
        self.assertEqual(result, desired)

    def test_properties(self):
        self.assertEqual(self.feed.name, 'テスト feed')
        self.assertEqual(self.feed.url, 'https://test.url/テスト')
        self.assertIsNone(self.feed.user_agent)
        self.assertTrue(self.feed.enabled)
        self.assertTrue(self.feed.magnet_enabled)
        self.assertTrue(self.feed.torrent_url_enabled)
        self.assertTrue(self.feed.torrent_file_enabled)
        self.assertTrue(self.feed.hide_torrent_filename_enabled)

    def test_torrent_url_for_entry(self):
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry['links'][1]['href'])

    def test_torrent_url_for_entry_when_no_link_with_torrent_mimetype(self):
        self.entry['links'][1]['type'] = 'application/x-not-bittorrent'
        self.assertEqual(self.feed.torrent_url_for_entry(self.entry),
                         self.entry['link'])

    def test_magnet_link_for_entry(self):
        self.assertEqual(self.feed.magnet_link_for_entry(self.entry),
                         self.entry['torrent_magneturi'])

    def test_magnet_link_for_entry_when_none_exists(self):
        del self.entry['torrent_magneturi']
        with self.assertRaises(KeyError):
            self.feed.magnet_link_for_entry(self.entry)

    def test_download_entry_torrent_file(self):
        sha256 = hashlib.sha256(b'').hexdigest()

        response_mock = MagicMock()
        response_mock.content = b''

        def run():
            return self.feed.download_entry_torrent_file(
                url=None,
                rss_entry=self.entry,
                directory=pathlib.Path('テスト')
            )

        with patch('requests.get', return_value=response_mock) as get_mock, \
             patch.object(pathlib.Path, 'mkdir'), \
             patch.object(pathlib.Path, 'write_bytes'):
            path = run()
            self.assertEqual(path.as_posix(), 'テスト/{}.torrent'.format(sha256))
            get_mock.assert_called_with(None, headers={})

            self.feed.user_agent = 'テスト user agent'
            self.feed.hide_torrent_filename_enabled = False
            expected_path = 'テスト/{}.torrent'.format(self.entry['title'])
            path = run()
            get_mock.assert_called_with(None, headers={'User-Agent':
                                                       'テスト user agent'})
            self.assertEqual(path.as_posix(), expected_path)

class TestMinimalConfigSubscription(unittest.TestCase):
    def setUp(self):
        self.config = TestMinimalConfig.config_from_string()
        self.feed = self.config.feeds['テスト feed']
        self.sub = self.feed.subscriptions['テスト sub']

    def test_properties(self):
        self.assertEqual(self.sub.name, 'テスト sub')
        self.assertEqual(self.sub.regex.pattern, r'テスト filename ([0-9]+)')
        self.assertIsNone(self.sub.number)
        self.assertIs(self.sub.directory, torrentrss.TEMPORARY_DIRECTORY)
        self.assertIsInstance(self.sub.command, torrentrss.StartFileCommand)
        self.assertTrue(self.sub.enabled)

    def test_none_number_lower_than_everything(self):
        self.assertTrue(self.sub.has_lower_number_than(parse_version('01')))
        self.assertTrue(self.sub.has_lower_number_than(parse_version('S01E01')))

class TestCommand(unittest.TestCase):
    def test_path_substitution(self):
        command = torrentrss.Command(['command', '$PATH_OR_URL', '--option'])
        path = '/home/test/テスト'
        self.assertEqual(list(command.arguments_with_substituted_path(path)),
                         ['command', path, '--option'])

if __name__ == '__main__':
    unittest.main()
