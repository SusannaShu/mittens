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
  5. At night: calculates bedtime from sunrise, alerts to head home,
     and triggers iPhone and Macbook to shut down at bedtime

iPhone (just listens):
  - Email "MITTENS_ALARM"    → automation sets timer alarm
  - Email "MITTENS_DOWNTIME" → automation shuts down phone and laptop for bed
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
| `CALENDAR_IDS` | `all` (auto-discovers all calendars) |
| `SLEEP_HOURS` | `9` (bedtime = sunrise - 9h, `0` to disable) |
| `TZ` | `America/New_York` (your timezone) |
| `TIMEZONE` | `America/New_York` (must match `TZ`) |

> See [SECURITY.md](SECURITY.md) for security guidance.

### Step 4: iPhone Setup (10 min)

Open **Mail** app and add your **iCloud** account (iCloud gets instant push; Gmail does not).

**Settings → Privacy & Security → Location Services → Shortcuts**: set to "While Using the App" + **Precise Location** on.

<img src="docs/location_permission.png" width="250" alt="Location permission settings">

#### Shortcut: "Mittens Location"
1. **Get Current Location**
2. **Dictionary** — `lat`: Latitude, `lon`: Longitude
3. **Get Contents of URL** — POST to `https://YOUR-APP.up.railway.app/location?key=YOUR_KEY` with JSON body

#### Automation 1: Morning GPS (7 AM)
- **Trigger**: Time of Day → 7:00 AM
- **Action**: Run Shortcut → "Mittens Location"
- Run Immediately ✓

#### Automation 2: Location Request
- **Trigger**: Email → Sender (your FROM_EMAIL) → Subject Contains `MITTENS_LOCATION`
- **Action**: Run Shortcut → "Mittens Location"
- Run Immediately ✓

#### Automation 3: Alarm Trigger
- **Trigger**: Email → Sender (your FROM_EMAIL) → Subject Contains `MITTENS_ALARM`
- **Actions**:
  1. Start Timer (3 seconds)
  2. Show Notification (Content from email)
  3. Copy Content to clipboard
  4. Search in Google Maps (Open When Run ✓)
- Run Immediately ✓

#### Automation 4: Bedtime Shutdown
- **Trigger**: Email → Sender (your FROM_EMAIL) → Subject Contains `MITTENS_DOWNTIME`
- **Actions**:
  1. Turn Sleep Focus **On**
  2. Get Device Is Locked
  3. If Device Is Locked → do nothing (already in bed)
  4. Otherwise:
     - Show Notification: "GO TO BED or I'm shutting it down :)"
     - Wait 10 seconds
     - If Device Is Locked → great
     - Otherwise → Show Notification: "OK that's it for today" → **Shut Down** device
- Run Immediately ✓

<img src="docs/bedtime_shortcut.png" width="250" alt="GO TO BED shortcut">

<img src="docs/automations.png" width="250" alt="iPhone automations"> <img src="docs/alarm_shortcut.png" width="250" alt="Alarm shortcut actions">

#### What it looks like when it fires:

<img src="docs/alarm_notification.png" width="250" alt="Mittens alarm on lock screen"> <img src="docs/timer_alarm.png" width="250" alt="Timer alarm"> <img src="docs/google_maps.png" width="250" alt="Google Maps with address">

## Calendar Events

Add a **location** to your events. Mittens only monitors events with addresses:

- "Physical Therapy" at "123 Main St" → ✅ Monitored
- "CS 101" at "Warren Weaver Hall, NYU" → ✅ Monitored
- "Call with Mom" (no location) → Ignored

## Bedtime Enforcement

Mittens calculates your bedtime dynamically based on **sunrise**:

```
Bedtime = tomorrow's sunrise − SLEEP_HOURS
```

In NYC with `SLEEP_HOURS=9`:

| Season | Sunrise | Bedtime |
|--------|---------|--------|
| Summer | ~5:25 AM | ~8:25 PM |
| Spring/Fall | ~6:45 AM | ~9:45 PM |
| Winter | ~7:15 AM | ~10:15 PM |

**When you're away from home**, Mittens adds travel time:

```
9:45 PM bedtime, you're 30 min from home:

  8:15 PM → MITTENS_ALARM "Head home for bed!"
            (bedtime − travel − 30 min get-ready buffer)
  9:15 PM → MITTENS_DOWNTIME
            (triggers iPhone to shut down)
  9:45 PM → Should be home & asleep 😴
```

**When you're home**, only the MITTENS_DOWNTIME fires — your iPhone gives you a 10-second warning, then shuts itself down:

<img src="docs/bedtime_shortcut.png" width="400" alt="GO TO BED shortcut">

Uses the free [sunrise-sunset.org](https://sunrise-sunset.org) API. No API key needed.

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
location.py         → GPS location handling
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

---

> **GitHub Topics**: `personal-assistant` `ios-automation` `google-calendar` `productivity` `iphone-shortcuts` `python`
