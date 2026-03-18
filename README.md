# Mittens

**Your AI assistant that makes sure you actually show up.**

Mittens monitors your Google Calendar, tracks your iPhone's GPS, and sets off an **actual iPhone alarm** when you need to leave for an appointment but haven't moved.

Runs on Railway (free). No Twilio. No monthly costs.

## How It Works

```
Every 60 seconds:
  1. Check Google Calendar for events in the next 2 hours
  2. For events with a location → check your iPhone's GPS
  3. Calculate: can you make it in time?
  4. If not → send ntfy push → iPhone Automation sets an ALARM
     "GET UP - Physical Therapy in 20 min"
```

## Architecture

```
Railway (free tier)                    Your iPhone
┌─────────────────────┐                ┌──────────────────┐
│  mittens.py          │   GPS POST    │  Shortcut:       │
│  - Flask server     │◄──────────────│  Send location   │
│  - Calendar poller  │    every 5m    │  every 5 min     │
│  - Travel calc      │               │                  │
│  - Alert logic      │   ntfy push   │  Automation:     │
│                     │──────────────►│  On ntfy notif → │
│                     │               │  Set Alarm +     │
│                     │               │  Show Alert      │
└─────────────────────┘               └──────────────────┘
```

## Setup (30 min total)

### Step 1: Google Calendar API (10 min)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable the **Google Calendar API**
4. Go to **Credentials** → Create **OAuth 2.0 Client ID** (Desktop app)
5. Download the JSON → save as `credentials.json`

### Step 2: Get Google Token (5 min)

Run this on your MacBook (one time only):

```bash
pip install google-auth-oauthlib google-api-python-client
python auth_helper.py
```

A browser opens → log in → copy the token JSON it prints.

### Step 3: ntfy Setup (2 min)

1. Install [ntfy app](https://apps.apple.com/app/ntfy/id1625396347) on iPhone
2. Open it → Subscribe to a topic (pick something unique like `mittens-yourname-xyz789`)
3. That's it. Remember the topic name.

### Step 4: Deploy to Railway (10 min)

1. Push this code to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add these **environment variables** in Railway:

| Variable | Value |
|----------|-------|
| `MITTENS_API_KEY` | Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `GOOGLE_TOKEN_JSON` | The token JSON from Step 2 |
| `GOOGLE_CREDENTIALS_JSON` | Contents of credentials.json |
| `NTFY_TOPIC` | Your ntfy topic (pick something unguessable like `mittens-j4k8x-a7b2m9q`) |
| `GOOGLE_MAPS_API_KEY` | (Optional) For accurate travel times |
| `BUFFER_MINUTES` | `5` (how early to alert before you need to leave) |
| `CALENDAR_IDS` | `primary` (or comma-separated calendar IDs) |

> See [SECURITY.md](SECURITY.md) for important security guidance.

4. Railway auto-deploys. Check the health endpoint: `https://your-app.up.railway.app/`

### Step 5: iPhone Shortcuts (10 min)

You need TWO things on your iPhone:

#### A) Shortcut: "Send Location to Mittens"

Create a new Shortcut with these actions:

1. **Get Current Location**
2. **Dictionary**
   - Key: `lat` → Value: `Shortcut Input.Current Location.Latitude`
   - Key: `lon` → Value: `Shortcut Input.Current Location.Longitude`
3. **Get Contents of URL**
   - URL: `https://your-app.up.railway.app/location?key=YOUR_API_KEY`
   - Method: **POST**
   - Headers: `Content-Type` = `application/json`
   - Body: **JSON** → the dictionary from step 2

#### B) Automation: "Run Location Update Every 5 Min"

Go to Automations tab → New Automation:

- **Trigger**: Time of Day → set for a time you wake up (e.g., 7 AM)
- **Action**: Run Shortcut → "Send Location to Mittens"
- **Repeat**: Turn on repeat, set to every 5 minutes
- **Turn OFF** "Ask Before Running"

*(Alternatively, you can create multiple time-based automations throughout the day)*

#### C) Automation: "Mittens Alarm Trigger"

This is the magic part. Create another Automation:

- **Trigger**: Notification → App: ntfy → Contains: `MITTENS_ALARM`
- **Actions**:
  1. **Set Alarm** → Create new alarm, label from notification
  2. **Show Alert** → Show the notification body as a big alert
  3. *(Optional)* **Speak Text** → "Get up! You have an appointment!"
- **Turn OFF** "Ask Before Running"

## Calendar Events

Add a **location** to your Google Calendar events. Mittens only monitors events with addresses:

- "Physical Therapy" at "123 Main St, New York, NY 10001" → Mittens monitors this
- "CS 101 Lecture" at "Warren Weaver Hall, NYU" → Mittens monitors this
- "Call with Mom" (no location) → Ignored
- "Lunch" (no location) → Ignored

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Health check |
| `/location` | POST | Receive GPS from iPhone `{"lat": x, "lon": y}` |
| `/location` | GET | Debug: see current location |
| `/test` | POST | Send test ntfy notification |
| `/stats` | GET | View attendance stats |

## Testing

```bash
# Send a test notification
curl -X POST "https://your-app.up.railway.app/test?key=YOUR_API_KEY"

# Check health (no key needed)
curl https://your-app.up.railway.app/

# Manually send a location
curl -X POST "https://your-app.up.railway.app/location?key=YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"lat": 40.7128, "lon": -74.0060}'

# Check stats
curl "https://your-app.up.railway.app/stats?key=YOUR_API_KEY"
```

## Local Development

```bash
# Set env vars
export NTFY_TOPIC="mittens-yourname-xyz789"
export GOOGLE_TOKEN_JSON='{"token": "...", ...}'
export GOOGLE_CREDENTIALS_JSON='...'

# Run
pip install -r requirements.txt
python mittens.py
```

## Files

```
mittens.py          → Main app (Flask server + background monitor)
calendar_client.py  → Google Calendar API
travel.py           → Travel time calculation (Maps API or estimate)
alerts.py           → ntfy push notifications
memory.py           → SQLite attendance tracking
scheduler.py        → Quiet hours + adaptive polling (future)
auth_helper.py      → One-time Google OAuth (run locally)
Procfile            → Railway deployment config
```

## Costs

| Service | Cost |
|---------|------|
| Railway | Free (500 hrs/mo) |
| ntfy | Free |
| Google Calendar API | Free |
| Google Maps API | Free tier (40k/mo) or skip (uses estimates) |
| **Total** | **$0/month** |

## Future

- [ ] Gmail integration (auto-detect appointment confirmations)
- [ ] ESP32-S3 camera for diet/exercise tracking
- [ ] Pattern learning (more aggressive for appointments you tend to miss)
- [ ] LLM layer for natural language memory queries
- [ ] iCloud Find My integration (skip the Shortcut for location)
