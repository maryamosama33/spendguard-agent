from unittest.mock import MagicMock, patch

from spendguard.extraction import extract_expense
from spendguard.models import ExtractedFields


def _mock_response(fields: ExtractedFields) -> MagicMock:
    response = MagicMock()
    response.parsed = fields
    return response


def test_extract_expense_complete_fields(tmp_path):
    sample = tmp_path / "invoice.jpg"
    sample.write_bytes(b"fake-image-bytes")

    fields = ExtractedFields(
        date="2026-09-30",
        amount=3000.0,
        supplier="Al-Nasr Sand Co",
        project="Site A",
        cost_item="transport",
        requester="Ahmed",
        invoice_number="INV-001",
        confidence=0.95,
    )

    with patch("spendguard.extraction._client") as mock_client_factory:
        mock_client_factory.return_value.models.generate_content.return_value = _mock_response(fields)
        expense = extract_expense(str(sample), source_channel="whatsapp", sender="2010000000")

    assert expense.missing_fields == []
    assert expense.amount == 3000.0
    assert expense.source_channel == "whatsapp"
    assert expense.sender == "2010000000"
    assert expense.source_file == str(sample)


def test_extract_expense_missing_fields_not_guessed(tmp_path):
    sample = tmp_path / "invoice.jpg"
    sample.write_bytes(b"fake-image-bytes")

    fields = ExtractedFields(
        date="2026-09-30",
        amount=3000.0,
        supplier=None,
        project=None,
        cost_item="transport",
        requester="Ahmed",
        confidence=0.4,
    )

    with patch("spendguard.extraction._client") as mock_client_factory:
        mock_client_factory.return_value.models.generate_content.return_value = _mock_response(fields)
        expense = extract_expense(str(sample), source_channel="email", sender="supplier@example.com")

    assert set(expense.missing_fields) == {"supplier", "project"}
    assert expense.supplier is None
    assert expense.project is None


def test_extract_expense_unknown_mime_type(tmp_path):
    sample = tmp_path / "invoice.unknownext"
    sample.write_bytes(b"data")

    try:
        extract_expense(str(sample), source_channel="whatsapp", sender="201")
    except ValueError as e:
        assert "MIME type" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown mime type")
