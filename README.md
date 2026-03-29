# Mittens 🐱

**Susanna's fully personalized AI assistant** — built to keep her on track, on time, and well-rested.

Mittens runs 24/7 on a server, watches Susanna's calendar, tracks her location, calculates when she needs to leave, and — when it's bedtime — literally shuts down her devices. It also auto-schedules her daily health rhythm (meals, sunrise, bedtime) into her Google Calendar, all tuned to the natural light cycle.

Runs on Railway (free). Uses Resend email (free). No monthly costs.

## What Mittens Does

### 🗓️ Never Miss an Appointment
Monitors all of Susanna's Google calendars and calculates biking time from her current GPS location. When it's time to leave, Mittens fires an alarm on her iPhone — escalating from notification → alarm → alarm again if she doesn't move.

### 🌅 Sunrise-Based Health Rhythm
Every day, Mittens fetches the sunrise time and auto-creates a full health schedule in the **Health** calendar:

| Event | Timing | Purpose |
|-------|--------|---------|
| 🌅 Sunrise | Sunrise | Natural wake time |
| 🍳 Breakfast | Sunrise | Eat within 30 min of waking |
| 🥗 Lunch | Sunrise + 6h | Midday fuel |
| 🍽️ Dinner | Sunrise + 12h | Last meal |
| 😴 Bedtime | Sunrise − 9h − 30min | Wind down (lights out 30 min later) |

Schedules 3 days ahead. Shifts naturally with seasons — earlier meals in summer, later in winter.

### 🛏️ Bedtime Enforcement
When it's time to sleep, Mittens doesn't just remind — it **forces compliance**:

1. **Away from home?** Fires a `MITTENS_ALARM` accounting for travel time + 30 min to get ready
2. **30 min before bed:** Sends `MITTENS_DOWNTIME` email → triggers iPhone automation
3. **iPhone automation:** Warns "GO TO BED or I'm shutting it down :)" → 10 seconds → **shuts down the device**

```
9:45 PM bedtime, 30 min from home:

  8:15 PM → MITTENS_ALARM "Head home for bed!"
  9:15 PM → MITTENS_DOWNTIME → activates Sleep Focus
          → 10 sec warning → device shuts down 💀
  9:45 PM → Lights out 😴
```

Bedtime = tomorrow's sunrise − `SLEEP_HOURS`. No fixed schedule — it shifts with the sun.

<img src="docs/bedtime_shortcut.png" width="400" alt="GO TO BED shortcut — shuts down phone if not locked after warning">

## How It Works

```
Railway Server (runs 24/7)              Susanna's iPhone
┌──────────────────────────┐            ┌────────────────────────┐
│  mittens.py               │            │                        │
│  ┌─ Calendar monitor     │            │  Mail Automations:     │
│  │  checks events + GPS  │            │                        │
│  ├─ Travel calculator    │  Email:    │  MITTENS_ALARM         │
│  │  biking time to venue │  triggers  │  → Timer + Alert       │
│  ├─ Health scheduler     │──────────►│  → Open Google Maps    │
│  │  meals + bedtime      │  (iCloud)  │                        │
│  ├─ Sunrise API          │            │  MITTENS_DOWNTIME      │
│  │  seasonal rhythms     │            │  → Sleep Focus ON      │
│  └─ Alert escalation     │            │  → Shut Down device    │
│                          │            │                        │
│                          │  GPS POST  │  7 AM daily:           │
│                          │◄──────────│  → Send location       │
└──────────────────────────┘            └────────────────────────┘
     Resend (free)                           Shortcuts app
```

> **Note**: Emails must go to an **iCloud** address — Apple Mail only does instant push for iCloud. Gmail uses fetch (15-30 min delay).

## Setup

### Step 1: Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Calendar API**
3. **Credentials** → Create **OAuth 2.0 Client ID** (Desktop app)
4. Download JSON → save as `credentials.json`

### Step 2: Get Google Token

```bash
pip install google-auth-oauthlib google-api-python-client
python auth_helper.py
```

Browser opens → log in → copy the token JSON. The scope includes read + write (for creating health events).

### Step 3: Deploy to Railway

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
| `HOME_LAT` | Your home latitude |
| `HOME_LON` | Your home longitude |
| `TRAVEL_MODE` | `bicycling` (or `driving`, `walking`, `transit`) |
| `BUFFER_MINUTES` | `5` |
| `CALENDAR_IDS` | `all` (auto-discovers all calendars) |
| `SLEEP_HOURS` | `9` (bedtime = sunrise - 9h, `0` to disable) |
| `TZ` | `America/New_York` |
| `TIMEZONE` | `America/New_York` |
| `HEALTH_CALENDAR` | `Health` (name of calendar for meals/sleep events) |

> See [SECURITY.md](SECURITY.md) for security guidance.
> See [SETUP_NOTES.md](SETUP_NOTES.md) for gotchas and troubleshooting.

### Step 4: iPhone Automations

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
- **Trigger**: Email → Subject Contains `MITTENS_LOCATION`
- **Action**: Run Shortcut → "Mittens Location"
- Run Immediately ✓

#### Automation 3: Alarm Trigger
- **Trigger**: Email → Subject Contains `MITTENS_ALARM`
- **Actions**:
  1. Start Timer (3 seconds)
  2. Show Notification (Content from email)
  3. Copy Content to clipboard
  4. Search in Google Maps (Open When Run ✓)
- Run Immediately ✓

#### Automation 4: Bedtime Shutdown
- **Trigger**: Email → Subject Contains `MITTENS_DOWNTIME`
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

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | No | Health check |
| `/location` | POST | Key | Receive GPS `{"lat": x, "lon": y}` |
| `/location` | GET | Key | Debug: see current location |
| `/test` | POST | Key | Send test email |
| `/stats` | GET | Key | View attendance stats |

All authenticated endpoints require `?key=YOUR_API_KEY`.

## Files

```
mittens.py          → Flask server + background monitor + health scheduler
calendar_client.py  → Google Calendar API (read events + create health events)
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
| Sunrise API | Free (no key needed) |
| Google Maps API | Free tier or skip (uses estimates) |
| **Total** | **$0/month** |

---

> **GitHub Topics**: `personal-assistant` `ios-automation` `google-calendar` `health` `productivity` `iphone-shortcuts` `python`
