import io
import time
import pathlib
import tempfile
import unittest
import collections
from unittest.mock import patch

from . import _torrentrss as torrentrss

class TestCommand(unittest.TestCase):
    def test_path_substitution(self):
        command = torrentrss.Command(['command', '$PATH_OR_URL', '--option'])
        path = '/home/test/テスト'
        self.assertEqual(list(command.arguments_with_substituted_path(path)),
                         ['command', path, '--option'])

minimal_config = '''{
    "feeds": {
        "テスト feed": {
            "url": "テスト url",
            "subscriptions": {
                "テスト sub": {
                    "pattern": "テスト regex ([0-9]+)"
                }
            }
        }
    }
}'''

class TestMinimalConfig(unittest.TestCase):
    # no need to test if the config passes the schema validation,
    # as that validation is done every setUp call
    def setUp(self):
        with patch('io.open', return_value=io.StringIO(minimal_config)):
            self.config = torrentrss.Config()

    def test_properties(self):
        self.assertIsNone(self.config.exception_gui)
        self.assertTrue(self.config.remove_old_log_files_enabled)
        self.assertEqual(self.config.log_file_limit,
                         torrentrss.DEFAULT_LOG_FILE_LIMIT)
        self.assertIs(self.config.default_directory,
                      torrentrss.TEMPORARY_DIRECTORY)
        self.assertIsInstance(self.config.default_command,
                              torrentrss.StartFileCommand)

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
                self.assertEqual(self.config._log_paths_by_newest_first(),
                                 list(reversed(log_paths.values())))
                self.config.remove_old_log_files()

            self.assertFalse(log_paths[logs[0]].exists())
            self.assertFalse(log_paths[logs[1]].exists())
            self.assertFalse(log_paths[logs[1]].parent.exists())
            self.assertFalse(log_paths[logs[2]].exists())
            self.assertTrue(log_paths[logs[3]].exists())
            self.assertTrue(log_paths[logs[4]].exists())

if __name__ == '__main__':
    unittest.main()
