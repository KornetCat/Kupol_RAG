import tempfile
import unittest
from pathlib import Path
import zipfile

from kupol_agent.credentials import load_team_keys


class CredentialTests(unittest.TestCase):
    def test_only_selected_team_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Dome_RAG-main/Keys/team_4.txt', 'Team: TEST_DOME_4\nDeepSeek: TEST_LLM_4')
                archive.writestr('Dome_RAG-main/Keys/team_1.txt', 'Team: OTHER\nDeepSeek: OTHER')
            self.assertEqual(load_team_keys(path, 4), {'dome_key': 'TEST_DOME_4', 'deepseek_api_key': 'TEST_LLM_4'})
            with self.assertRaises(ValueError):
                load_team_keys(path, 3)

    def test_error_does_not_include_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Keys/team_4.txt', 'DeepSeek: SECRET SPACE')
            with self.assertRaises(ValueError) as error:
                load_team_keys(path, 4)
            self.assertNotIn('SECRET', str(error.exception))
