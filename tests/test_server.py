"""Tests for allstar_mcp.server — no live API required."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import allstar_mcp.server as srv
from allstar_mcp.server import (
    connect_node,
    disconnect_node,
    disconnect_all,
    execute_macro,
    health_check,
    send_dtmf,
    _validate_dtmf,
    _validate_macro_number,
    _validate_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CLEAR_VARS = {"txkeyed": False, "rxkeyed": False, "num_links": 1}
ACTIVE_TX   = {"txkeyed": True,  "rxkeyed": False, "num_links": 1}
ACTIVE_RX   = {"txkeyed": False, "rxkeyed": True,  "num_links": 1}


def _patch_vars(variables: dict):
    """Context manager: make _get('/variables') return `variables`."""
    return patch.object(srv, "_get", return_value=variables)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestValidateNode:
    def test_digits_accepted(self):
        _validate_node("637050")  # must not raise

    def test_alpha_rejected(self):
        with pytest.raises(ValueError, match="digits only"):
            _validate_node("abc")

    def test_mixed_rejected(self):
        with pytest.raises(ValueError):
            _validate_node("123abc")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            _validate_node("")

    def test_leading_zero_accepted(self):
        _validate_node("002560")  # valid node number format


class TestValidateDtmf:
    def test_digits_accepted(self):
        _validate_dtmf("1234")

    def test_star_hash_accepted(self):
        _validate_dtmf("*123#")

    def test_all_valid_chars(self):
        _validate_dtmf("0123456789*#")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_dtmf("")

    def test_letter_rejected(self):
        with pytest.raises(ValueError, match="valid characters"):
            _validate_dtmf("12A3")

    def test_special_char_rejected(self):
        with pytest.raises(ValueError):
            _validate_dtmf("12!3")

    def test_space_rejected(self):
        with pytest.raises(ValueError):
            _validate_dtmf("1 2")


class TestValidateMacroNumber:
    def test_digits_accepted(self):
        _validate_macro_number("1")
        _validate_macro_number("42")

    def test_alpha_rejected(self):
        with pytest.raises(ValueError, match="digits only"):
            _validate_macro_number("abc")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            _validate_macro_number("")


class TestToolInputValidation:
    """Validation fires before any confirmed/dry_run check."""

    def test_connect_rejects_bad_node(self):
        with pytest.raises(ValueError):
            connect_node("bad!", confirmed=False)

    def test_disconnect_rejects_bad_node(self):
        with pytest.raises(ValueError):
            disconnect_node("bad!", confirmed=False)

    def test_send_dtmf_rejects_bad_sequence(self):
        with pytest.raises(ValueError):
            send_dtmf("12!3", confirmed=False)

    def test_execute_macro_rejects_bad_number(self):
        """Macro validation runs before the confirmed check."""
        with pytest.raises(ValueError):
            execute_macro("bad!", confirmed=True)

    def test_execute_macro_rejects_bad_number_even_unconfirmed(self):
        with pytest.raises(ValueError):
            execute_macro("bad!", confirmed=False)


# ---------------------------------------------------------------------------
# Confirmed-False guard (no API calls should be made)
# ---------------------------------------------------------------------------

class TestConfirmedFalseGuard:
    """Every confirmed-action tool must return not_executed when confirmed=False."""

    def test_connect_node(self):
        result = connect_node("2560", confirmed=False)
        assert result["status"] == "not_executed"
        assert "confirmed=False" in result["reason"]

    def test_disconnect_node(self):
        result = disconnect_node("2560", confirmed=False)
        assert result["status"] == "not_executed"
        assert "confirmed=False" in result["reason"]

    def test_send_dtmf(self):
        result = send_dtmf("123", confirmed=False)
        assert result["status"] == "not_executed"

    def test_execute_macro(self):
        result = execute_macro("1", confirmed=False)
        assert result["status"] == "not_executed"

    def test_disconnect_all(self):
        result = disconnect_all(confirmed=False)
        assert result["status"] == "not_executed"
        assert "confirmed=False" in result["reason"]


# ---------------------------------------------------------------------------
# dry_run — preview without API calls
# ---------------------------------------------------------------------------

class TestDryRun:
    """dry_run=True must return a preview dict and never call _post."""

    def _assert_dry_run(self, result: dict, expected_action: str) -> None:
        assert result["status"] == "dry_run"
        assert result["action"] == expected_action
        assert "would_send" in result
        assert "note" in result

    def test_connect_node_dry_run(self):
        result = connect_node("2560", dry_run=True)
        self._assert_dry_run(result, "POST /connect")
        assert result["would_send"]["node"] == "2560"
        assert result["would_send"]["monitor_only"] is False

    def test_connect_node_dry_run_monitor_only(self):
        result = connect_node("2560", monitor_only=True, dry_run=True)
        assert result["would_send"]["monitor_only"] is True

    def test_disconnect_node_dry_run(self):
        result = disconnect_node("2560", dry_run=True)
        self._assert_dry_run(result, "POST /disconnect")
        assert result["would_send"]["node"] == "2560"

    def test_send_dtmf_dry_run(self):
        result = send_dtmf("*123#", dry_run=True)
        self._assert_dry_run(result, "POST /dtmf")
        assert result["would_send"]["sequence"] == "*123#"

    def test_execute_macro_dry_run(self):
        result = execute_macro("5", dry_run=True)
        self._assert_dry_run(result, "POST /macro")
        assert result["would_send"]["macro_number"] == "5"

    def test_disconnect_all_dry_run(self):
        result = disconnect_all(dry_run=True)
        self._assert_dry_run(result, "POST /disconnect-all")

    def test_dry_run_does_not_require_confirmed(self):
        # dry_run=True, confirmed=False — must still return dry_run (not not_executed)
        result = connect_node("2560", confirmed=False, dry_run=True)
        assert result["status"] == "dry_run"

    def test_dry_run_skips_api(self):
        # _post must NOT be called when dry_run=True
        with patch.object(srv, "_post") as mock_post:
            connect_node("2560", dry_run=True)
            mock_post.assert_not_called()

    def test_dry_run_validates_input_first(self):
        # Validation still runs before dry_run short-circuit
        with pytest.raises(ValueError):
            connect_node("bad!", dry_run=True)

        with pytest.raises(ValueError):
            send_dtmf("bad!", dry_run=True)

        with pytest.raises(ValueError):
            execute_macro("bad!", dry_run=True)


# ---------------------------------------------------------------------------
# Active-QSO guard
# ---------------------------------------------------------------------------

class TestActiveQsoGuard:
    """connect_node and disconnect_node must block when the node is transmitting/receiving."""

    def test_connect_blocked_on_txkeyed(self):
        with _patch_vars(ACTIVE_TX):
            result = connect_node("2560", confirmed=True)
        assert result["status"] == "blocked"
        assert result["txkeyed"] is True

    def test_connect_blocked_on_rxkeyed(self):
        with _patch_vars(ACTIVE_RX):
            result = connect_node("2560", confirmed=True)
        assert result["status"] == "blocked"
        assert result["rxkeyed"] is True

    def test_connect_override_bypasses_block(self):
        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            result = connect_node("2560", confirmed=True, override_active_qso=True)
        mock_post.assert_called_once()
        assert result == {"ok": True}

    def test_disconnect_blocked_on_txkeyed(self):
        with _patch_vars(ACTIVE_TX):
            result = disconnect_node("2560", confirmed=True)
        assert result["status"] == "blocked"

    def test_disconnect_blocked_on_rxkeyed(self):
        with _patch_vars(ACTIVE_RX):
            result = disconnect_node("2560", confirmed=True)
        assert result["status"] == "blocked"

    def test_connect_proceeds_when_clear(self):
        with patch.object(srv, "_get", return_value=CLEAR_VARS), \
             patch.object(srv, "_post", return_value={"connected": True}) as mock_post:
            result = connect_node("2560", confirmed=True)
        mock_post.assert_called_once_with("/connect", {"node": "2560", "monitor_only": False})
        assert result == {"connected": True}

    def test_dry_run_skips_qso_check(self):
        # dry_run=True must NOT call _get for variables
        with patch.object(srv, "_get") as mock_get:
            result = connect_node("2560", dry_run=True)
        mock_get.assert_not_called()
        assert result["status"] == "dry_run"


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    PING_RESPONSE = {
        "service": "ASL3-API",
        "node": "637050",
        "callsign": "KJ5IRQ",
        "ami_connected": True,
        "sse_clients": 0,
    }
    VERSION_RESPONSE = {"version": "1.4.1"}

    def _mock_healthy(self):
        """Return mocks for a fully healthy API."""
        mock_no_auth = MagicMock(side_effect=[self.PING_RESPONSE, self.VERSION_RESPONSE])
        mock_get = MagicMock(return_value={"features": []})
        return mock_no_auth, mock_get

    def test_returns_all_expected_keys(self):
        mock_no_auth, mock_get = self._mock_healthy()
        with patch.object(srv, "_get_no_auth", mock_no_auth), \
             patch.object(srv, "_get", mock_get):
            result = health_check()
        for key in ("api_reachable", "auth_ok", "ami_connected", "node", "callsign", "api_version", "error"):
            assert key in result, f"Missing key: {key}"

    def test_healthy_state(self):
        mock_no_auth, mock_get = self._mock_healthy()
        with patch.object(srv, "_get_no_auth", mock_no_auth), \
             patch.object(srv, "_get", mock_get):
            result = health_check()
        assert result["api_reachable"] is True
        assert result["auth_ok"] is True
        assert result["ami_connected"] is True
        assert result["node"] == "637050"
        assert result["callsign"] == "KJ5IRQ"
        assert result["api_version"] == "1.4.1"
        assert result["error"] is None

    def test_api_unreachable(self):
        import httpx
        with patch.object(srv, "_get_no_auth", side_effect=httpx.ConnectError("refused")):
            result = health_check()
        assert result["api_reachable"] is False
        assert result["auth_ok"] is False
        assert "unreachable" in result["error"].lower()

    def test_auth_failure(self):
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 403
        auth_error = httpx.HTTPStatusError("403", request=MagicMock(), response=mock_response)

        mock_no_auth = MagicMock(side_effect=[self.PING_RESPONSE, self.VERSION_RESPONSE])
        with patch.object(srv, "_get_no_auth", mock_no_auth), \
             patch.object(srv, "_get", side_effect=auth_error):
            result = health_check()
        assert result["api_reachable"] is True
        assert result["auth_ok"] is False
        assert "ALLSTAR_API_KEY" in result["error"]

    def test_version_failure_does_not_fail_check(self):
        """If /version is down, health_check should still succeed."""
        mock_no_auth = MagicMock(side_effect=[self.PING_RESPONSE, Exception("timeout")])
        mock_get = MagicMock(return_value={})
        with patch.object(srv, "_get_no_auth", mock_no_auth), \
             patch.object(srv, "_get", mock_get):
            result = health_check()
        assert result["api_reachable"] is True
        assert result["auth_ok"] is True
        assert result["api_version"] is None  # not populated, but no error


# ---------------------------------------------------------------------------
# events_stream_info — must never contain the real API key
# ---------------------------------------------------------------------------

class TestEventsStreamInfo:
    def test_placeholder_not_real_key(self):
        import os
        from allstar_mcp.server import events_stream_info
        os.environ["ALLSTAR_API_KEY"] = "super-secret-key-12345"
        try:
            result = events_stream_info()
            assert "super-secret-key-12345" not in result
            assert "<ALLSTAR_API_KEY>" in result
        finally:
            del os.environ["ALLSTAR_API_KEY"]
