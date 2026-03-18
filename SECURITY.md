# Security

## For users deploying Mittens

### Required: Set an API Key

Generate a key and set it as `MITTENS_API_KEY` in your environment:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

This key protects your `/location`, `/stats`, and `/test` endpoints.
Without it, anyone who finds your URL can track your location or spam you with fake alerts.

### How to include the key in requests

Your iPhone Shortcut URL becomes:
```
https://your-app.up.railway.app/location?key=YOUR_API_KEY_HERE
```

Or use the Authorization header:
```
Authorization: Bearer YOUR_API_KEY_HERE
```

### Required: Use a unique ntfy topic

Your ntfy topic acts like a channel name. Pick something unguessable:
- Bad:  `mittens`
- Bad:  `mittens-john`
- Good: `mittens-j4k8x-a7b2m9q`

Anyone who knows your topic can send you notifications. Treat it like a password.

### Environment variables checklist

| Variable | Contains secrets? | Notes |
|----------|:-:|-------|
| `MITTENS_API_KEY` | YES | Auth for all endpoints |
| `GOOGLE_TOKEN_JSON` | YES | OAuth tokens for your calendar |
| `GOOGLE_CREDENTIALS_JSON` | YES | OAuth client secrets |
| `GOOGLE_MAPS_API_KEY` | YES | Billed to your Google account |
| `NTFY_TOPIC` | Semi | Unguessable topic = security |
| `CALENDAR_IDS` | No | |
| `BUFFER_MINUTES` | No | |
| `POLL_INTERVAL` | No | |

### What NOT to do

- Never commit `.env` files, `credentials.json`, or `token.pickle`
- Never put your API key in code (use env vars)
- Never share your Railway URL + API key together publicly
- Never use a guessable ntfy topic

### Data Mittens stores

- Your GPS coordinates (last known only, in memory — lost on redeploy)
- Appointment check history (SQLite — also lost on Railway redeploy)
- No passwords, no financial data, no personal files

### Reporting vulnerabilities

If you find a security issue, please open a GitHub issue or contact the maintainer directly.
