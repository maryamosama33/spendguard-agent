"""Push messages to Telegram chats outside the current conversation (F14).

An engineer's request reaches the owner in the owner's own chat, and the
owner's decision reaches the engineer. Hermes only replies in the chat a
message came from, so SpendGuard calls the Bot API itself (stdlib only).
Every function returns False instead of raising: a failed push must not lose
the expense, and the caller then replies in the current chat instead.
"""

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1024
TIMEOUT_SECONDS = 20


def owner_ids() -> list[str]:
    raw = os.environ.get("SPENDGUARD_OWNER_IDS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and owner_ids())


def is_owner(sender: str | None) -> bool:
    return bool(sender) and sender in owner_ids()


def _multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode("utf-8"))
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8"))
    parts.append(path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _call(method: str, fields: dict[str, str], document: Path | None = None) -> bool:
    url = API.format(token=os.environ.get("TELEGRAM_BOT_TOKEN", ""), method=method)
    if document is None:
        body, content_type = json.dumps(fields).encode("utf-8"), "application/json"
    else:
        body, content_type = _multipart(fields, "document", document)
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return bool(json.load(response).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False  # never log the error: the request URL contains the bot token


def send_message(chat_id: str, text: str) -> bool:
    return _call("sendMessage", {"chat_id": chat_id, "text": text})


def send_with_document(chat_id: str, text: str, document: str | None) -> bool:
    """The text as the document's caption (one message), or plain text if
    there is no file or the text is too long for a caption."""
    path = Path(document) if document else None
    if path is None or not path.is_file():
        return send_message(chat_id, text)
    if len(text) <= CAPTION_LIMIT:
        return _call("sendDocument", {"chat_id": chat_id, "caption": text}, path)
    return _call("sendDocument", {"chat_id": chat_id}, path) and send_message(chat_id, text)


def send_to_owners(text: str, document: str | None = None) -> bool:
    """True if at least one owner received it."""
    delivered = [send_with_document(owner, text, document) for owner in owner_ids()]
    return any(delivered)
