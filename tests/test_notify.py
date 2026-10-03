import urllib.error

from spendguard import notify


def test_network_failure_returns_false_instead_of_raising(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    def offline(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(notify.urllib.request, "urlopen", offline)

    assert notify.send_message("1", "x") is False


def test_missing_document_is_sent_as_text(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(notify, "_call", lambda method, fields, document=None: calls.append(method) or True)

    assert notify.send_with_document("1", "x", str(tmp_path / "gone.pdf")) is True
    assert calls == ["sendMessage"]


def test_document_sent_with_text_as_caption(monkeypatch, tmp_path):
    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"%PDF")
    calls = []
    monkeypatch.setattr(notify, "_call",
                        lambda method, fields, document=None: calls.append((method, fields, document)) or True)

    notify.send_with_document("1", "طلب صرف", str(invoice))

    assert calls == [("sendDocument", {"chat_id": "1", "caption": "طلب صرف"}, invoice)]


def test_multipart_body_carries_fields_and_file(tmp_path):
    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"%PDF-data")

    body, content_type = notify._multipart({"caption": "طلب"}, "document", invoice)

    assert content_type.startswith("multipart/form-data; boundary=")
    assert "طلب".encode("utf-8") in body and b"%PDF-data" in body
    assert b'filename="invoice.pdf"' in body and b"Content-Type: application/pdf" in body


def test_not_configured_without_owner_ids(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SPENDGUARD_OWNER_IDS", "")

    assert notify.telegram_configured() is False
