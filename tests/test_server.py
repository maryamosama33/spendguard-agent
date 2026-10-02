from unittest.mock import patch

from google.genai import errors as genai_errors

from spendguard import server


def _api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "message": "x"}})


def test_extract_expense_quota_error_is_retryable_dict():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(429)):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is True
    assert "429" in result["error"]


def test_extract_expense_bad_request_is_not_retryable():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(400)):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is False


def test_extract_expense_missing_file_returns_error():
    result = server.extract_expense("does/not/exist.pdf", "whatsapp", "201")

    assert result == {"error": "File not found: does/not/exist.pdf", "retryable": False}
