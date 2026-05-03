import os
import unittest
from unittest.mock import patch

from aider.main import main


class TestBrowser(unittest.TestCase):
    @patch("aider.main.launch_gui")
    def test_browser_flag_imports_streamlit(self, mock_launch_gui):
        os.environ["AIDER_ANALYTICS"] = "false"

        # Run main with --browser and --yes flags
        main(["--browser", "--yes"])

        # Check that launch_gui was called
        mock_launch_gui.assert_called_once()

        # Try to import streamlit
        try:
            import streamlit  # noqa: F401

            streamlit_imported = True
        except ImportError:
            streamlit_imported = False

        # Assert that streamlit was successfully imported
        self.assertTrue(
            streamlit_imported, "Streamlit should be importable after running with --browser flag"
        )

    @patch("aider.desktop.launch_desktop_gui")
    @patch("aider.main.check_desktop_install", return_value=True)
    @patch("aider.main.check_streamlit_install", return_value=True)
    def test_desktop_flag_launches_desktop_gui(
        self,
        _mock_streamlit_check,
        _mock_desktop_check,
        mock_launch_desktop_gui,
    ):
        os.environ["AIDER_ANALYTICS"] = "false"

        main(["--desktop", "--yes"])

        mock_launch_desktop_gui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
