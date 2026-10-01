import mimetypes
from pathlib import Path

from google import genai
from google.genai import types

from spendguard.models import Expense, ExtractedFields

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


def _client() -> genai.Client:
    return genai.Client()  # reads GEMINI_API_KEY from env


def _mime_type_for(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        raise ValueError(f"Could not determine MIME type for {path}")
    return mime_type


def _request_fields(path: Path, mime_type: str) -> ExtractedFields:
    file_part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)
    response = _client().models.generate_content(
        model="gemini-2.5-flash",
        contents=[file_part, EXTRACTION_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFields,
        ),
    )
    return response.parsed


def _missing_fields(fields: ExtractedFields) -> list[str]:
    return [f for f in REQUIRED_FIELDS if getattr(fields, f) in (None, "")]


def extract_expense(file_path: str, source_channel: str, sender: str) -> Expense:
    """Extract structured expense fields from a photo, PDF, or scanned form."""
    path = Path(file_path)
    fields = _request_fields(path, _mime_type_for(path))
    return Expense(
        **fields.model_dump(),
        missing_fields=_missing_fields(fields),
        source_channel=source_channel,
        sender=sender,
        source_file=str(path),
    )
