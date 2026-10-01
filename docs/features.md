# SpendGuard — Feature List

## MVP (build these — see CLAUDE.md)

### Document Intake
- F01 Multi-channel intake: receives expense requests as images, PDFs, text on WhatsApp; reads supplier invoices from email attachments.
- F02 Voice-note requests: understands Egyptian-Arabic voice notes from site engineers (e.g. "paid 3,000 EGP sand transport for project X") and turns them into requests.
- F03 Arabic handwriting OCR: reads handwritten Arabic expense forms photographed on a phone, including skewed or poorly lit images.

### Extraction & Organization
- F04 Structured field extraction: extracts date, amount, supplier, project, cost item, requester, invoice number into structured JSON.
- F05 Auto-classification: assigns each expense to the correct project and BOQ cost item (materials, labor, transport, subcontractor).
- F06 Source document link: stores a link to the original file next to every spreadsheet row for review/audit.

### Validation & Error Handling
- F07 Missing-field follow-up: asks the sender for missing info instead of guessing (e.g. "Which project is this for?").
- F08 Unreadable image handling: requests a new photo when confidence is low or the image is unreadable.
- F09 Duplicate detection: flags invoices submitted twice, even by different people.

### Supplier Price Intelligence
- F10 Supplier price memory: keeps a history of unit prices per item per supplier over time.
- F11 Price anomaly alerts: alerts when a price is abnormally high vs. recent history (e.g. "steel is 18% above the last 3 purchases").

### Approval Workflow
- F14 Approval before logging: sends a request summary on WhatsApp for approve/reject; rows are written only after approval.

### Q&A
- F23 Natural-language questions: answers questions in Egyptian Arabic by text or voice (e.g. "How much did we spend on project X this month?").

### Demo & Deployment
- F25 Judge demo mode: runs end to end from the Hermes CLI with seeded sample data, no WhatsApp setup needed.
- F26 Security guardrails: restricts the agent to business tools only; accepts messages from approved numbers only.

## Phase 2 (out of scope unless asked)

- F12 Supplier comparison — suggest cheaper suppliers for the same item.
- F13 Monthly savings report — total value of price discrepancies/duplicates caught per month.
- F15 Amount-based approval routing.
- F16 Pending-approval reminders.
- F17 Audit trail (who requested/approved/when).
- F18 Budget vs. actual tracking.
- F19 Budget threshold alerts.
- F20 Invoice-request reconciliation matching.
- F21 Proactive payment reminders.
- F22 Cash-flow forecast.
- F24 Automatic weekly summary.
