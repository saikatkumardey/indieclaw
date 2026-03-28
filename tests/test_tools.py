"""Tool unit tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_httpx_client(ok=True, text="Unauthorized"):
    """Return a mock httpx.Client class simulating a context manager."""
    resp = MagicMock()
    resp.is_success = ok
    resp.text = text

    client = MagicMock()
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    return MagicMock(return_value=client)


def test_send_telegram_success():
    from indieclaw.tools import _send_telegram
    with patch("indieclaw.tools.httpx.Client", _mock_httpx_client(ok=True)):
        result = _send_telegram(chat_id="123", message="hi")
        assert result.startswith("Sent.")


def test_send_telegram_failure():
    from indieclaw.tools import _send_telegram
    with patch("indieclaw.tools.httpx.Client", _mock_httpx_client(ok=False)):
        assert "Failed" in _send_telegram(chat_id="123", message="hi")

