# Mittens Setup Notes & Gotchas

Quick reference for configuration quirks that are easy to forget.

---

## Timezone (CRITICAL on Railway)

Railway runs in **UTC**. Without setting the timezone, `datetime.now()` returns UTC and sunrise calculations are wrong.

**Must set TWO env vars on Railway:**

| Variable | Value | Why |
|----------|-------|-----|
| `TZ` | `America/New_York` | Makes `datetime.now()` return EDT/EST |
| `TIMEZONE` | `America/New_York` | Used by sunrise API conversion |

**Symptom if missing:** Bedtime shows as 1:42 AM instead of 9:42 PM (sunrise is kept in UTC → subtracted 9h from 10:42 AM UTC instead of 6:42 AM EDT).

---

## Google Calendar IDs

**Use `CALENDAR_IDS=all`** to auto-discover all calendars.

Without this, only `primary` is monitored — events from accepted calendar invites (school email, shared calendars, etc.) are missed.

| Setting | What's monitored |
|---------|-----------------|
| `CALENDAR_IDS=primary` | Only your main Gmail calendar |
| `CALENDAR_IDS=all` | All calendars (auto-discovered via Google API) |
| `CALENDAR_IDS=primary,abc@group.calendar.google.com` | Specific calendars |

**Auto-skip:** Calendars with "Holiday" or "Birthday" in the name are automatically excluded to reduce noise.

**Symptom if wrong:** Events you accepted from other people or from secondary calendars don't trigger alerts.

---

## Email Must Be iCloud

Alerts go via email. Apple Mail only does **instant push** for iCloud addresses.

| Email | Push speed | Works? |
|-------|-----------|--------|
| `@icloud.com` | Instant | ✅ |
| `@gmail.com` | 15-30 min fetch | ❌ Too slow |

Set `TO_EMAIL` to your iCloud email.

---

## "Already Home" Detection

The bicycling fallback estimator always adds 3-4 min prep time, even at 0 distance. The "already home" threshold is **5 min** to account for this.

If you switch `TRAVEL_MODE` to `driving` (which has higher prep time), you may need to adjust this in `_check_bedtime()`.

---

## Bedtime = Sunrise-Based

Bedtime is **not a fixed time**. It's calculated dynamically:

```
Bedtime = tomorrow's sunrise - SLEEP_HOURS
```

| Env var | Default | Description |
|---------|---------|-------------|
| `SLEEP_HOURS` | `0` (disabled) | Hours of sleep target |

**Examples (NYC, SLEEP_HOURS=9):**

| Season | Sunrise | Bedtime |
|--------|---------|---------|
| Summer (Jun) | ~5:25 AM | ~8:25 PM |
| Spring/Fall (Mar) | ~6:45 AM | ~9:45 PM |
| Winter (Dec) | ~7:15 AM | ~10:15 PM |

**API used:** [sunrise-sunset.org](https://api.sunrise-sunset.org) (free, no key). Cached once per day.

---

## Bedtime Alerts Timeline

When you're **away from home** near bedtime:

```
Bedtime = 9:30 PM, you're 30 min away from home

8:30 PM  →  MITTENS_ALARM "Bedtime (head home!)"
             (bedtime - travel - 30 min prep)
9:00 PM  →  MITTENS_DOWNTIME email
             (triggers iPhone Sleep Focus via Shortcuts)
9:30 PM  →  Should be home & in bed
```

When you're **home**: only the MITTENS_DOWNTIME fires (no travel alarm needed).

---

## iPhone Shortcut Automations

| Trigger | Shortcut | Purpose |
|---------|----------|---------|
| Email subject `MITTENS_LOCATION` | Send GPS to Mittens | Location request |
| Email subject `MITTENS_ALARM` | Set timer, show alert, open Maps | Leave-now alarm |
| Email subject `MITTENS_ZOOM` | Check if Zoom is open, alarm if not | Virtual meeting |
| Email subject `MITTENS_DOWNTIME` | Activate Sleep Focus, dim screen | Bedtime lockdown |
| 7:00 AM daily | Send GPS to Mittens | Morning location seed |

### MITTENS_DOWNTIME Shortcut (GO TO BED)

```
Get Device Is Locked
If Device Is Locked → do nothing
Otherwise:
  → Show notification "GO TO BED or I'm shutting it down"
  → Wait 10 seconds
  → If Device Is Locked → good
  → Otherwise → Shut Down device
```

---

## Calendar Sync (Webhooks vs Polling)

Mittens uses a **cache + webhook** strategy instead of polling Google Calendar every tick:

1. **Startup**: Fetches all events once and caches them
2. **Webhooks**: Google Calendar push notifications (`/calendar/webhook` endpoint) tell Mittens when events are created, updated, or deleted → cache is invalidated → next tick re-fetches
3. **Safety fallback**: Cache auto-expires every 15 min (re-fetches even without a webhook)
4. **Background loop**: Still runs every `POLL_INTERVAL` seconds, but reads from cache (no API call unless cache is dirty)

**To enable webhooks**, set `WEBHOOK_BASE_URL` to your Railway public URL. Without it, Mittens uses the 15-min fallback only.

**Webhook channels expire every 24h** and are automatically renewed every 20h.

---

## Railway Environment Variables (Complete List)

| Variable | Required | Example | Notes |
|----------|----------|---------|-------|
| `MITTENS_API_KEY` | ✅ | `mdlXQX4sg-Zvl2...` | Auth for iPhone requests |
| `RESEND_API_KEY` | ✅ | `re_FCEbLx...` | Email sending |
| `FROM_EMAIL` | ✅ | `system@yourdomain.com` | Must be verified in Resend |
| `TO_EMAIL` | ✅ | `you@icloud.com` | Must be iCloud for instant push |
| `GOOGLE_TOKEN_JSON` | ✅ | `{"token": "..."}` | From `auth_helper.py` |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | `{"installed": {...}}` | From Google Cloud Console |
| `HOME_LAT` | ✅ | `40.73374` | Home latitude |
| `HOME_LON` | ✅ | `-73.97520` | Home longitude |
| `TZ` | ✅ | `America/New_York` | System timezone |
| `TIMEZONE` | ✅ | `America/New_York` | Sunrise timezone |
| `CALENDAR_IDS` | ⚡ | `all` | Use `all` for auto-discover |
| `SLEEP_HOURS` | ⚡ | `9` | 0 = disabled |
| `WEBHOOK_BASE_URL` | ⚡ | `https://mittens.up.railway.app` | Enables real-time calendar sync |
| `BUFFER_MINUTES` | | `5` | Minutes early for events |
| `POLL_INTERVAL` | | `60` | Travel check frequency (seconds) |
| `TRAVEL_MODE` | | `bicycling` | driving/walking/transit |
| `GOOGLE_MAPS_API_KEY` | | (empty) | Optional, for accurate travel |

✅ = required, ⚡ = strongly recommended
