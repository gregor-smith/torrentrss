import io
import unittest
import unittest.mock

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

    @unittest.mock.patch('io.open', return_value=io.StringIO(minimal_config))
    def setUp(self, mock_open):
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

if __name__ == '__main__':
    unittest.main()
