# Changelog

## 0.1.0 (unreleased)

Initial read-only release.

- Config flow with password login, supported two-factor challenge, reauth and
  reconfigure flows.
- Account discovery, explicit child/institution pairing and bounded profile
  probing for ambiguous accounts.
- Sensors for unseen notifications, today's/tomorrow's menu, today's calendar
  status, latest returned diary date and per-date diary count.
- Opt-in experimental institution event calendar with strict non-recurring
  filtering and a partial-calendar repair issue.
- Admin-only bounded read services and a coalesced refresh service.
- Redacted diagnostics and opaque registry identifiers.
- Shared institution and child devices across guardian accounts: one device and
  one entity set per real object, with per-account notification entities.
- Services resolve shared devices to an available loaded account.
- Config-entry migration to remove old account-scoped institution/child devices
  and avoid duplicate entities after upgrading.
