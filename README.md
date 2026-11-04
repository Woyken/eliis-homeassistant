# Eliis for Home Assistant

Unofficial, read-only Home Assistant integration for [Eliis](https://eliis.eu)
kindergartens. It is intended for parents and guardians who want neutral
summaries of what their kindergarten has made visible in Eliis.

This project is not affiliated with, endorsed by or supported by Eliis. It uses
an undocumented mobile API that may change at any time. Use it at your own risk
and keep the Eliis app as the authoritative source.

## What it provides

- **Unseen notifications** — the count Eliis reports, with no invented zero.
- **Today's and tomorrow's menu** — sanitized text returned for the current and
  next local date. Missing content is reported as `no_content_returned`, never
  as a claim that no menu was published.
- **Calendar status today** — the explicit source label for today's child
  calendar entry, if one exists. This is not an attendance or presence claim.
- **Latest returned diary date** and a count for that date — a summary of the
  newest fetched guardian-feed page. It is not a publication timestamp.
- **Experimental institution calendar** (opt-in) — only explicitly
  non-recurring events with unambiguous times. Recurring, ambiguous and
  unparseable records are omitted and reported through a repair issue.

The integration is read-only. It does not create, update or delete anything in
Eliis.

## Requirements

- Home Assistant 2026.10 or later.
- An Eliis guardian account that can sign in to the Eliis app.
- Two-factor authentication is supported only when Eliis returns a challenge
  identifier that can be completed with a code. Other challenges abort setup
  with a clear error.

## Installation

### HACS (custom repository)

1. In HACS, open **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Woyken/eliis-homeassistant` as an **Integration**.
3. Install **Eliis** and restart Home Assistant.

### Manual

Copy `custom_components/eliis` into your Home Assistant `config/custom_components`
directory and restart.

## Setup

1. Go to **Settings → Devices & services → Add integration → Eliis**.
2. Enter your Eliis username and password.
3. Complete the two-factor step if Eliis requests a code.
4. Review the options to choose modules, polling interval and optional child
   display names.

The password is not stored. The session cookie established by Eliis is stored in
the config entry so the integration can keep polling. That cookie is included in
Home Assistant backups and is visible to anyone with access to `.storage`. Treat
it like a password.

### Reauthentication and reconfiguration

When Eliis rejects the stored session, Home Assistant asks you to sign in again.
The existing account, institutions and selections are kept. Reconfiguration
verifies the signed-in account, refreshes visible institutions/children and
keeps only selections that are still visible.

## Services

All services are admin-only and read-only. They return data with
`return_response: true`.

| Service | Target | Purpose |
| --- | --- | --- |
| `eliis.get_menu` | Institution device | Bounded menu days (max 31 days, max 3 week requests). |
| `eliis.get_feed_summary` | Child device | Date and count summaries for up to 3 feed pages. |
| `eliis.get_status_history` | Child device | Status-log records for one explicit date. |
| `eliis.refresh` | Config entry | Coalesced refresh of one module or the whole account. |

Service responses include `coverage.complete` and `coverage.truncated` so callers
can avoid treating a partial answer as complete.

## Experimental calendar limitations

The institution calendar is opt-in and deliberately conservative:

- Only events with an explicit non-recurring marker, no recurrence rule and a
  classifiable start/end are exposed.
- All-day intervals follow observed Eliis export behaviour: an end date is
  treated as inclusive and converted to an exclusive Home Assistant date.
- Malformed or unsupported records are omitted and counted in a repair issue.
- A query needing more than three 31-day windows is refused instead of returning
  a partial calendar.
- Events discovered after an automation's trigger time cannot trigger
  retrospectively.

Do not use this calendar as the only source for time-critical reminders.

## Data, privacy and diagnostics

- No credentials, child names, raw Eliis IDs or response bodies are written to
  logs by the integration.
- Entity registry and unique IDs use opaque SHA-256-derived identifiers.
- Diagnostics redact the username, session cookie and account/child identifiers.
- Menu text and event text are stripped of HTML and bounded in length.
- Missing, null and malformed values remain distinguishable.

## Troubleshooting

- **Invalid authentication** — verify the credentials in the Eliis app. Some
  accounts require a two-factor step.
- **Cannot connect** — check Home Assistant's internet access and whether Eliis
  is reachable.
- **Unsupported response** — Eliis changed a response shape. Open an issue with
  redacted diagnostics; do not paste cookies or children's data.
- **Calendar repair issue** — some events cannot be represented safely. The
  count is shown; use the Eliis app for the authoritative calendar.

## Development

```bash
python -m pip install -r requirements_test.txt
PYTHONPATH=. pytest -q
ruff check custom_components tests
ruff format --check custom_components tests
```

The test suite uses fictional fixtures and a local HTTP server. CI never talks
to Eliis.

## License

MIT. See [LICENSE](LICENSE).
