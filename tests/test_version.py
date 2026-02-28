"""Tests for indieclaw.version — shared version utilities."""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch


class TestLocalVersion:
    def test_returns_version_string(self):
        from indieclaw.version import local_version
        ver = local_version()
        assert re.match(r"\d+\.\d+\.\d+", ver) or ver == "unknown"

    def test_importlib_metadata_success(self):
        from indieclaw.version import local_version
        with patch("indieclaw.version.importlib.metadata.version", return_value="1.2.3"):
            assert local_version() == "1.2.3"

    def test_falls_back_to_uv_tool_list(self):
        from indieclaw.version import local_version
        mock_result = MagicMock()
        mock_result.stdout = "indieclaw v2.0.0\n- indieclaw\n"
        with patch("indieclaw.version.importlib.metadata.version", side_effect=Exception), \
             patch("indieclaw.version.subprocess.run", return_value=mock_result):
            assert local_version() == "2.0.0"

    def test_returns_unknown_when_all_fail(self):
        from indieclaw.version import local_version
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("indieclaw.version.importlib.metadata.version", side_effect=Exception), \
             patch("indieclaw.version.subprocess.run", return_value=mock_result), \
             patch("indieclaw.version.Path") as mock_path:
            mock_path.return_value.__truediv__ = MagicMock(return_value=MagicMock(exists=lambda: False))
            assert local_version() == "unknown"


class TestGetUpdateSummary:
    def test_returns_version_transition(self):
        from indieclaw.version import get_update_summary
        mock_result = MagicMock()
        mock_result.stdout = "indieclaw v3.0.0\n"
        mock_result.returncode = 1  # force fallback

        mock_list = MagicMock()
        mock_list.stdout = "indieclaw v3.0.0\n- indieclaw\n"

        with patch("indieclaw.version.subprocess.run", side_effect=[mock_result, mock_list]):
            summary = get_update_summary("git+https://github.com/test/repo", "2.0.0")
        assert "2.0.0" in summary
        assert "3.0.0" in summary

    def test_handles_network_failure_gracefully(self):
        from indieclaw.version import get_update_summary
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("indieclaw.version.subprocess.run", return_value=mock_result):
            summary = get_update_summary("git+https://github.com/test/repo", "1.0.0")
        assert "1.0.0" in summary
        assert "unknown" in summary


class TestCheckRemoteVersion:
    def test_returns_version_when_available(self):
        from indieclaw.version import check_remote_version
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'version = "1.5.0"'
        mock_requests = MagicMock()
        mock_requests.get.return_value = mock_resp
        with patch.dict("sys.modules", {"requests": mock_requests}):
            assert check_remote_version("git+https://github.com/test/repo") == "1.5.0"

    def test_returns_none_on_failure(self):
        from indieclaw.version import check_remote_version
        mock_requests = MagicMock()
        mock_requests.get.side_effect = Exception("no network")
        with patch.dict("sys.modules", {"requests": mock_requests}):
            assert check_remote_version("git+https://github.com/test/repo") is None

    def test_returns_none_for_non_github_source(self):
        from indieclaw.version import check_remote_version
        assert check_remote_version("https://example.com/package.tar.gz") is None
