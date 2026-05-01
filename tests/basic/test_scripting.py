import unittest
from pathlib import Path
from unittest.mock import patch

from aider.coders import Coder
from aider.coders.base_coder import CoderResult
from aider.models import Model
from aider.utils import GitTemporaryDirectory


class TestScriptingAPI(unittest.TestCase):
    @patch("aider.coders.base_coder.Coder.send")
    def test_basic_scripting(self, mock_send):
        with GitTemporaryDirectory():
            # Setup
            def mock_send_side_effect(messages, functions=None):
                coder.partial_response_content = "Changes applied successfully."
                coder.partial_response_function_call = None
                return "Changes applied successfully."

            mock_send.side_effect = mock_send_side_effect

            # Test script
            fname = Path("greeting.py")
            fname.touch()
            fnames = [str(fname)]
            model = Model("gpt-4-turbo")
            coder = Coder.create(main_model=model, fnames=fnames)

            result1 = coder.run("make a script that prints hello world")
            result2 = coder.run("make it say goodbye")

            # Assertions
            self.assertEqual(mock_send.call_count, 2)
            self.assertEqual(result1, "Changes applied successfully.")
            self.assertEqual(result2, "Changes applied successfully.")

    @patch("aider.coders.base_coder.Coder.send")
    def test_structured_scripting_output(self, mock_send):
        with GitTemporaryDirectory():
            def mock_send_side_effect(messages, functions=None):
                coder.partial_response_content = "Completed edits."
                coder.partial_response_function_call = None
                return "Completed edits."

            mock_send.side_effect = mock_send_side_effect

            fname = Path("main.py")
            fname.write_text("print('hello')\n")
            model = Model("gpt-4-turbo")
            coder = Coder.create(main_model=model, fnames=[str(fname)], auto_commits=False)
            coder.aider_edited_files = {str(fname)}

            result = coder.run_structured("update greeting", include_diff=False)

            self.assertIsInstance(result, CoderResult)
            self.assertEqual(result.summary, "Completed edits.")


if __name__ == "__main__":
    unittest.main()
