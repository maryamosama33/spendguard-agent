from datetime import date
from unittest.mock import MagicMock, patch

from spendguard.extraction import extract_expense, extract_expense_from_text
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


def test_extract_expense_from_text_sends_transcript_and_today(tmp_path):
    fields = ExtractedFields(date="2026-10-01", amount=3000.0, supplier="النصر للنقل والتوريدات",
                             project="فيلات التجمع الخامس", cost_item="transport", confidence=0.9)
    transcript = "دفعت 3000 جنيه نقل رمل امبارح"

    with patch("spendguard.extraction._client") as mock_client_factory:
        generate = mock_client_factory.return_value.models.generate_content
        generate.return_value = _mock_response(fields)
        expense = extract_expense_from_text(transcript, "whatsapp", "201001112222", today=date(2026, 10, 2))

    prompt_text = generate.call_args.kwargs["contents"][0]
    assert transcript in prompt_text and "Today is 2026-10-02" in prompt_text
    assert expense.amount == 3000.0
    assert expense.missing_fields == ["requester"]
    assert expense.source_file is None
    assert expense.sender == "201001112222"


def test_known_item_names_are_offered_to_gemini():
    fields = ExtractedFields(item="sand transport")

    with patch("spendguard.extraction._client") as mock_client_factory:
        generate = mock_client_factory.return_value.models.generate_content
        generate.return_value = _mock_response(fields)
        extract_expense_from_text("نقل رمل بـ 3000", "whatsapp", "201",
                                  known_items=["cement", "sand transport"])

    prompt = generate.call_args.kwargs["contents"][1]
    assert '"cement", "sand transport"' in prompt


def test_no_known_items_hint_without_history():
    with patch("spendguard.extraction._client") as mock_client_factory:
        generate = mock_client_factory.return_value.models.generate_content
        generate.return_value = _mock_response(ExtractedFields())
        extract_expense_from_text("x", "whatsapp", "201")

    assert "price history" not in generate.call_args.kwargs["contents"][1]


def test_extract_expense_unknown_mime_type(tmp_path):
    sample = tmp_path / "invoice.unknownext"
    sample.write_bytes(b"data")

    try:
        extract_expense(str(sample), source_channel="whatsapp", sender="201")
    except ValueError as e:
        assert "MIME type" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown mime type")
