# SpendGuard — Hackathon (72h, deadline Sat Oct 3 2026)

## What we're building

An AI agent that controls SME spending as it happens. Field staff send
expense requests on WhatsApp (photos of handwritten Arabic forms, PDFs,
Egyptian-Arabic voice notes); suppliers and staff can also send invoices
and requests by email (PDF/image attachments or email body). The agent
extracts the data, checks for duplicates and abnormal supplier prices,
asks the owner for approval, then logs approved expenses to a project
spreadsheet. It also answers spending questions in Egyptian Arabic.

First pilot: an Egyptian construction company. Works for any SME with
field spending (maintenance, supply, logistics, restaurants).
Hackathon: "Agents at Work", judged on: it works end to end, hours
saved, money saved.

## Architecture

- Hermes Agent (installed separately, NOT in this repo): conversation,
  WhatsApp/email gateway, voice transcription (Egyptian-Arabic STT),
  scheduling. Configured via ~/.hermes/config.yaml + SOUL.md.
- THIS repo: a Python MCP server (mcp SDK v2.x, MCPServer) exposing
  business-logic tools that Hermes calls. Both channels (WhatsApp/
  email) feed the same MCP tools; every saved expense records its
  source channel and sender.
- Gemini (google-genai, structured output) for vision extraction,
  including Arabic handwriting.
- Storage: SQLite is the source of truth; approved rows only are
  mirrored to Google Sheets (gspread).
- WhatsApp: Baileys bridge for the demo, Cloud API for production.

## MVP scope: build ONLY these (see docs/features.md)

F01–F11, F14, F23, F25, F26. Phase 2 features are out of scope unless
I ask.

## Structure

```
spendguard/server.py      MCPServer, registers tools
spendguard/models.py      Pydantic models (Expense, PriceRecord)
spendguard/extraction.py  Gemini vision -> Expense JSON
spendguard/storage.py     SQLite + Sheets sync
spendguard/checks.py      duplicate + price anomaly logic
spendguard/query.py       answers spending questions
data/seed/                sample invoices, projects.json, price history
tests/
```

## MCP tools (MVP)

extract_expense, check_duplicate, check_price_anomaly, save_expense
(status=pending), approve_expense, reject_expense, query_expenses

## Agent flow

1. Receive a request (WhatsApp or email)
2. extract_expense → if fields are missing or confidence is low, ask
   the sender instead of guessing (F07/F08)
3. check_duplicate → flag if it's a repeat (F09)
4. check_price_anomaly → flag if the unit price is abnormally high vs.
   recent history (F10/F11)
5. save_expense with status=pending, send the owner a one-message
   summary with a link to the source document (F06/F14)
6. On approval → approve_expense writes the row to Google Sheets
   On rejection → reject_expense records the reason

## Conventions

- Python 3.11+, venv at ./venv (Windows: venv\Scripts\activate)
- Every tool returns JSON-serializable dicts; all text UTF-8 (Arabic
  must work)
- Never guess missing fields: return them in a "missing_fields" list
- Secrets only in .env; never print, log, hardcode, or commit them;
  .env.example lists the variable names with no values
- Keep functions small; write a pytest test for every check
- Replies to users are in Egyptian Arabic, in a single message

## Commands

- Run server: `python -m spendguard.server`
- Inspect tools: `mcp dev spendguard/server.py`
- Tests: `pytest`
- Seed demo data: `python -m spendguard.seed` (loads data/seed/price_history.json
  as approved historical expenses; skips if the DB already has data, pass
  `--force` to reseed)

## Rules

- MVP only. Do not build Phase 2 features unless I ask.
- Never write to Sheets before approval.
- Keep it simple; a judge must run it from the README in under 5
  minutes.
- Seed data must make the price-anomaly demo work on first run.
- Ask me before adding new dependencies.
- Build one tool at a time; I test and commit between steps.
