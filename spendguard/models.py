from pydantic import BaseModel, Field


class ExtractedFields(BaseModel):
    """Fields Gemini extracts directly from a document/text."""

    date: str | None = None
    amount: float | None = None
    currency: str = "EGP"
    supplier: str | None = None
    project: str | None = None
    cost_item: str | None = None  # materials | labor | transport | subcontractor
    item: str | None = None  # specific line item, e.g. "steel rebar 12mm", "sand"
    requester: str | None = None
    invoice_number: str | None = None
    confidence: float = 0.0
    image_quality: str | None = None  # clear | blurry | unreadable (documents only)


class Expense(ExtractedFields):
    """Full expense record, including fields attached by the caller."""

    missing_fields: list[str] = Field(default_factory=list)
    source_channel: str | None = None  # telegram | whatsapp | email
    sender: str | None = None
    source_file: str | None = None
    status: str = "pending"  # pending | approved | rejected
    rejection_reason: str | None = None
    id: int | None = None
