# fuzzynet Umbrel community app store

Add `https://github.com/fuzzynet/umbrel-app-store` in umbrelOS under App Store → ⋯ → Community App Stores.

## Office Dashboard Feed (`fuzzynet-office-dash`)

The feed for the tvhome launcher's "Office dashboard" screensaver, on port **8790**.

- Image: the official `python:3.13-alpine` (multi-arch), pinned by digest. The app code is `feed.py.template`.
  umbrelOS renders it to `feed.py` on install and on every update (the file has no `$`, so envsubst leaves it unchanged).
  The code uses only the Python standard library.
- State lives in `${APP_DATA_DIR}/data`: pushed revenue totals, the calendar, photos and `config.json` (mode 600).
- Nothing secret is in this repository. Access tokens arrive after install through
  `POST /provision`. That request carries an RSA-signed payload, and only the admin box holds the signing key.
  `DASH_PROVISION_KEY` in the compose file is the public half.

### Endpoints

| Path | Auth |
|---|---|
| `GET /` | umbrelOS login (status page, no secrets) |
| `GET /feed.json` | `?token=` read token |
| `GET /photos/<id>.jpg` | none |
| `POST /calendar` | `Authorization: Bearer <upload token>` |
| `POST /revenue` | `Authorization: Bearer <upload token>`. Body `{"revenue": {today_cents, mrr_cents, mrr_label, ytd_cents, all_time_cents, updated_epoch, ...}, "new_payments": [{id, amount_cents, account, description, created_epoch}]}`. This is the only revenue source: the app does not poll any payment provider (a legacy Stripe mode runs only if `DASH_STRIPE_ENABLED=1`). The TV hides revenue more than 15 minutes old. |
| `POST /provision` | signed payload |
| `GET /health` | none (ages and counts only) |

### Calendar from a private iCal URL (1.3.0)

If `AZTC_CALENDAR_ICS_URL` is provisioned through `POST /provision`, the feed fetches that Google
Calendar "secret address in iCal format" every 15 minutes (and right after local midnight) and writes
today's events (America/Phoenix; recurring, all-day, EXDATE and moved instances handled) to
`data/calendar.json` in the same format as `POST /calendar`. Pushes keep working as a fallback; the
newest write wins. `/health` reports `calendar.source` (`ics` or `push`), age and today's event count.
The URL lives only in `data/config.json` and is never logged or displayed.
