import mimetypes
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


def _request_fields(path: Path, mime_type: str) -> ExtractedFields:
    file_part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)
    # Keep the client referenced: if it is garbage-collected mid-call, google-genai
    # closes its HTTP client and the request fails with "client has been closed".
    client = _client()
    _gemini_limiter.acquire()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[file_part, EXTRACTION_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFields,
        ),
    )
    return response.parsed


def find_missing_fields(fields: ExtractedFields) -> list[str]:
    return [f for f in REQUIRED_FIELDS if getattr(fields, f) in (None, "")]


def extract_expense(file_path: str, source_channel: str, sender: str) -> Expense:
    """Extract structured expense fields from a photo, PDF, or scanned form."""
    path = Path(file_path)
    fields = _request_fields(path, _mime_type_for(path))
    return Expense(
        **fields.model_dump(),
        missing_fields=find_missing_fields(fields),
        source_channel=source_channel,
        sender=sender,
        source_file=str(path),
    )
