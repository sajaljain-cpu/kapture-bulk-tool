# Kapture Bulk Ticket Closer

Upload a CSV (`ticket_id`, `comment` columns), preview/validate it, test one row, then
bulk-run the Kapture "update ticket from other source" API against every row.

## Required environment variables

| Variable | Purpose |
|---|---|
| `KAPTURE_AUTH_TOKEN` | The full `Authorization` header value for Kapture, e.g. `Basic xxxxx...`. **Required** - the app refuses to start without it. |
| `APP_USERNAME` | Login username for this tool itself. |
| `APP_PASSWORD` | Login password for this tool itself. |

`APP_USERNAME` / `APP_PASSWORD` protect the tool with HTTP Basic Auth. If either is
missing the app still runs but logs a warning and serves **without any login** - only
acceptable when running strictly on your own machine, never when deployed publicly.

## Run locally

```bash
export KAPTURE_AUTH_TOKEN="Basic xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export APP_USERNAME="youruser"
export APP_PASSWORD="a-strong-password"
python kapture_bulk_tool.py
```

Opens at http://127.0.0.1:8765 (or `$PORT` if set).

## Deploy on Railway

1. Push this repo to GitHub.
2. Create a new Railway project from that repo.
3. In Railway's project settings, add the three environment variables above.
4. Railway auto-detects the `Procfile` and runs `python kapture_bulk_tool.py`, binding
   to the `PORT` Railway provides.
5. Once deployed, the app is reachable at your Railway-assigned URL and will prompt for
   the `APP_USERNAME` / `APP_PASSWORD` login before allowing any ticket updates.

## Safety notes

- Every "Run All" click sends real write requests to production Kapture tickets - there
  is no undo.
- Keep `APP_USERNAME` / `APP_PASSWORD` set on any hosted deployment; without them the
  tool has no access control and anyone with the URL could bulk-close real tickets.
- Never commit real credentials into this repo - they belong in environment variables
  only (see `.gitignore`).
