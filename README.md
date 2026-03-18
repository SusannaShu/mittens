# Mittens

**Your AI assistant that makes sure you actually show up.**

Mittens monitors your Google Calendar and tells your iPhone to **set an alarm** when you need to leave — all via email automations. Fully server-driven. Zero manual effort after setup.

Runs on Railway (free). Uses Resend email (free). No monthly costs.

## How It Works

```
Server (runs 24/7):
  1. Checks Google Calendar for events with locations
  2. Calculates biking time from your last known GPS
  3. When GPS is stale + event approaching → emails you to refresh location
  4. When you need to leave NOW → emails alarm trigger
  
iPhone (just listens):
  - Email "MITTENS_ALARM" → automation sets timer alarm
  - 7 AM daily → sends morning GPS to seed the day
  - No GPS? Falls back to home location
```

> **Note**: Emails must go to an **iCloud** address — Apple Mail only does instant push for iCloud. Gmail uses fetch (15-30 min delay).

## Architecture

```
Railway (free tier)                    Your iPhone
┌─────────────────────┐                ┌──────────────────┐
│  mittens.py          │                │                  │
│  - Calendar poller  │   Email:       │  Mail Automation: │
│  - Travel calc      │ MITTENS_ALARM  │  → Show Alert     │
│  - Home fallback    │──────────────►│  → Start Timer    │
│  - Alert logic      │  (via iCloud)  │    (3 sec alarm)  │
│                     │               │                  │
│                     │   GPS POST    │  7 AM Automation: │
│                     │◄──────────────│  → Send location  │
└─────────────────────┘               └──────────────────┘
     Resend (free)
```

## Setup (30 min total)

### Step 1: Google Calendar API (10 min)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Calendar API**
3. **Credentials** → Create **OAuth 2.0 Client ID** (Desktop app)
4. Download JSON → save as `credentials.json`

### Step 2: Get Google Token (5 min)

```bash
pip install google-auth-oauthlib google-api-python-client
python auth_helper.py
```

Browser opens → log in → copy the token JSON.

### Step 3: Deploy to Railway (10 min)

1. Push to GitHub, deploy from [railway.app](https://railway.app)
2. Add these **environment variables**:

| Variable | Value |
|----------|-------|
| `MITTENS_API_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `GOOGLE_TOKEN_JSON` | Token JSON from Step 2 |
| `GOOGLE_CREDENTIALS_JSON` | Contents of `credentials.json` |
| `RESEND_API_KEY` | Your [Resend](https://resend.com) API key |
| `FROM_EMAIL` | Sender email (must be verified in Resend) |
| `TO_EMAIL` | Your **iCloud** email (instant push in Mail app) |
| `HOME_LAT` | Your home latitude (GPS fallback) |
| `HOME_LON` | Your home longitude (GPS fallback) |
| `TRAVEL_MODE` | `bicycling` (or `driving`, `walking`, `transit`) |
| `BUFFER_MINUTES` | `5` |
| `CALENDAR_IDS` | `primary` |

> See [SECURITY.md](SECURITY.md) for security guidance.

### Step 4: iPhone Setup (10 min)

Open **Mail** app and add your **iCloud** account (iCloud gets instant push; Gmail does not).

#### Shortcut: "Mittens Location"
1. **Get Current Location**
2. **Dictionary** — `lat`: Latitude, `lon`: Longitude
3. **Get Contents of URL** — POST to `https://YOUR-APP.up.railway.app/location?key=YOUR_KEY` with JSON body

#### Automation 1: Morning GPS (7 AM)
- **Trigger**: Time of Day → 7:00 AM
- **Action**: Run Shortcut → "Mittens Location"
- Run Immediately ✓

#### Automation 2: Alarm Trigger
- **Trigger**: Email → Sender (your FROM_EMAIL) → Subject Contains `MITTENS_ALARM`
- **Actions**: Show Alert (email subject) + Start Timer (3 seconds)
- Run Immediately ✓

## Calendar Events

Add a **location** to your events. Mittens only monitors events with addresses:

- "Physical Therapy" at "123 Main St" → ✅ Monitored
- "CS 101" at "Warren Weaver Hall, NYU" → ✅ Monitored
- "Call with Mom" (no location) → Ignored

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | No | Health check |
| `/location` | POST | Key | Receive GPS `{"lat": x, "lon": y}` |
| `/location` | GET | Key | Debug: see current location |
| `/test` | POST | Key | Send test email |
| `/stats` | GET | Key | View attendance stats |

All authenticated endpoints require `?key=YOUR_API_KEY`.

## Testing

```bash
# Health check
curl https://your-app.up.railway.app/

# Send test email
curl -X POST "https://your-app.up.railway.app/test?key=API_KEY"

# Send location
curl -X POST "https://your-app.up.railway.app/location?key=API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"lat": 40.7128, "lon": -74.0060}'
```

## Files

```
mittens.py          → Flask server + background calendar monitor
calendar_client.py  → Google Calendar API integration
travel.py           → Travel time (bicycling/driving/walking/transit)
alerts.py           → Email alerts via Resend
memory.py           → SQLite attendance tracking
scheduler.py        → Adaptive polling intervals
auth_helper.py      → One-time Google OAuth (run locally)
```

## Costs

| Service | Cost |
|---------|------|
| Railway | Free (500 hrs/mo) |
| Resend | Free (100 emails/day) |
| Google Calendar API | Free |
| Google Maps API | Free tier or skip (uses estimates) |
| **Total** | **$0/month** |
