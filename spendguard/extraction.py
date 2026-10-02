import mimetypes
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from google import genai
from google.genai import types

from spendguard.models import Expense, ExtractedFields
from spendguard.ratelimit import RateLimiter

REQUIRED_FIELDS = ["date", "amount", "supplier", "project", "cost_item", "requester"]

EXTRACTION_PROMPT = """
You are extracting data from an SME expense request for an Egyptian
construction company. The document may be a handwritten Arabic form, a
printed invoice, or a photo of either, and may be skewed or poorly lit.

Extract these fields:
- date (ISO 8601, YYYY-MM-DD)
- amount (number, total amount)
- currency (default "EGP" unless another currency is clearly stated)
- supplier (company or person who was paid)
- project (the project/site this expense is for, if mentioned)
- cost_item (one of: materials, labor, transport, subcontractor)
- item (the specific thing paid for, e.g. "steel rebar 12mm", "sand",
  "cement"; use a short normalized name so the same item is named
  consistently across purchases)
- requester (who is submitting/requesting this expense)
- invoice_number (if present)

If a field is not present or you are not confident about it, leave it null.
Do not guess. Set "confidence" to your overall confidence (0.0-1.0) in the
extraction as a whole.
"""


# Flash-Lite: reads Arabic handwriting as well as gemini-3.8-flash in our tests,
# and the free tier caps 3.8-flash at 20 requests/day (too few for a demo).
GEMINI_MODEL = "gemini-3.5-flash-lite"

# Free tier allows 5 requests/minute; stay under it so callers wait instead of failing.
_gemini_limiter = RateLimiter(max_calls=4, window=60.0)


def _client() -> genai.Client:
    # Reads GEMINI_API_KEY from env. Retries with exponential backoff
    # (~5s, 10s, 20s) on 429/5xx: Gemini returns transient 503 "high demand"
    # errors, and 429 quota errors ask to retry after up to ~30s.
    retry = types.HttpRetryOptions(attempts=4, initial_delay=5.0, max_delay=30.0)
    return genai.Client(http_options=types.HttpOptions(retry_options=retry))


def _mime_type_for(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        raise ValueError(f"Could not determine MIME type for {path}")
    return mime_type


TEXT_CONTEXT = """
The request is the text below, not a document: a transcribed Egyptian-Arabic
voice note or the body of an email. Amounts may be written in words
("تلاتة آلاف" = 3000). Today is {today}; convert relative dates such as
"النهارده" (today) or "امبارح" (yesterday) to YYYY-MM-DD. The sender saying
"I paid" does not tell you their name: leave requester null unless a name is
given.

Request text:
{text}
"""


KNOWN_ITEMS_HINT = """
Items already in the price history: {items}.
If the item paid for is one of these (in any language or wording, e.g.
"نقل رمل" is "sand transport"), set "item" to that exact name, so its price
can be compared with past purchases. Otherwise use a new short English name.
"""


def _prompt(known_items: Sequence[str]) -> str:
    if not known_items:
        return EXTRACTION_PROMPT
    return EXTRACTION_PROMPT + KNOWN_ITEMS_HINT.format(items=", ".join(f'"{i}"' for i in known_items))


def _ask_gemini(content: types.Part | str, known_items: Sequence[str] = ()) -> ExtractedFields:
    # Keep the client referenced: if it is garbage-collected mid-call, google-genai
    # closes its HTTP client and the request fails with "client has been closed".
    client = _client()
    _gemini_limiter.acquire()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[content, _prompt(known_items)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFields,
        ),
    )
    return response.parsed


def _request_fields(path: Path, mime_type: str, known_items: Sequence[str] = ()) -> ExtractedFields:
    return _ask_gemini(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type), known_items)


def find_missing_fields(fields: ExtractedFields) -> list[str]:
    return [f for f in REQUIRED_FIELDS if getattr(fields, f) in (None, "")]


def extract_expense(
    file_path: str, source_channel: str, sender: str, known_items: Sequence[str] = ()
) -> Expense:
    """Extract structured expense fields from a photo, PDF, or scanned form."""
    path = Path(file_path)
    fields = _request_fields(path, _mime_type_for(path), known_items)
    return Expense(
        **fields.model_dump(),
        missing_fields=find_missing_fields(fields),
        source_channel=source_channel,
        sender=sender,
        source_file=str(path),
    )


def extract_expense_from_text(
    text: str, source_channel: str, sender: str,
    known_items: Sequence[str] = (), today: date | None = None,
) -> Expense:
    """Extract expense fields from a voice-note transcript or an email body."""
    today = today or date.today()
    fields = _ask_gemini(TEXT_CONTEXT.format(today=today.isoformat(), text=text), known_items)
    return Expense(
        **fields.model_dump(),
        missing_fields=find_missing_fields(fields),
        source_channel=source_channel,
        sender=sender,
    )
