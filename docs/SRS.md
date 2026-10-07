# Software Requirements Specification — DOLMA

| | |
|---|---|
| **Product** | DOLMA — conversational calendar assistant |
| **Version** | 1.0 (reverse-engineered from the code base) |
| **Basis** | `main` at commit `6272ce5` |
| **Structure** | Adapted from IEEE 830 / ISO/IEC/IEEE 29148 |

> This document was derived from the implementation rather than written before
> it. Each requirement states behaviour the code exhibits today and cites where it
> lives, so the specification can be checked against the source. Behaviour the
> code does *not* yet provide, but which the design implies, is collected
> separately in [Appendix B](#appendix-b--observed-gaps-and-candidate-requirements).

---

## 1. Introduction

### 1.1 Purpose

This SRS describes the functional and non-functional requirements of DOLMA, a web
application that lets a signed-in user manage their Google Calendar, check the
weather and find nearby places by chatting with an LLM-driven assistant. It is
intended for developers maintaining the system, assessors of the ELEC5620 project,
and anyone planning to extend DOLMA towards production use.

### 1.2 Scope

DOLMA consists of:

- a **React single-page application** (sign-up, sign-in, chat, settings), and
- a **Flask backend** that hosts a **ReAct agent** (Reason → Act → Observe) built
  on the OpenAI Chat Completions API, with six tools backed by Google Calendar,
  OpenStreetMap (Overpass / Nominatim), OpenWeatherMap / Open-Meteo, and ip-api.

Out of scope: booking, email, web browsing, goal tracking (removed), mobile native
clients, multi-calendar or multi-timezone scheduling.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| **Agent / ReAct loop** | The cycle in `backend/agent.py`: call the model with tool schemas; if it requests tools, run them, append the results, repeat. |
| **Tool** | A function the model may call (`find_events`, `create_event`, `update_event`, `delete_event`, `find_places`, `get_weather`). |
| **Observation** | A tool's result, sent back to the model as a `role: "tool"` message. |
| **UI payload** | Fields a tool produces for the browser to render (cards, chips). Never shown to the model. |
| **Write tool** | `create_event`, `update_event`, `delete_event`. |
| **Preview / Confirm** | Two-phase write: `confirm=false` returns a preview and changes nothing; `confirm=true` performs the write. |
| **Turn** | One `POST /api/chat` request, i.e. one user message and the agent's full response. |
| **Clash** | An existing timed event that strictly overlaps a proposed one. |
| **Sydney wall-clock time** | A local date-time in `Australia/Sydney` whose UTC offset is supplied by `zoneinfo`, not by the model. |

### 1.4 References

- `README.md` — architecture and setup
- `backend/` — `app.py`, `agent.py`, `tool_handlers.py`, `tools.py`, `google_calendar.py`, `places.py`, `weather.py`, `formatting.py`, `users.py`
- `frontend/src/` — `main.jsx`, `auth.js`, `RequireAuth.jsx`, `pages/*.jsx`
- `backend/tests/` — 51 offline tests
- `.github/workflows/ci.yml`, `docker-compose.yml`

---

## 2. Overall Description

### 2.1 Product Perspective

```text
 Browser (React SPA, :5173)
   │  fetch, credentials: include (session cookie)
   ▼
 Flask backend (:5000, :5050 under Docker)
   ├─ /api/auth/*        ── SQLite users.db
   ├─ /api/google/*      ── Google OAuth 2.0 (token.json)
   └─ /api/chat ── agent.run() ──► OpenAI Chat Completions
                        │
                        └─ dispatch(tool) ─┬─ Google Calendar API v3
                                           ├─ Overpass API (OSM)
                                           ├─ OpenWeatherMap → Open-Meteo
                                           ├─ Nominatim (reverse geocode)
                                           └─ ip-api.com (IP geolocation)
```

### 2.2 User Classes

| Class | Description |
|---|---|
| **Visitor** | Unauthenticated; may see the landing page, sign up and sign in. |
| **Registered user** | Signed in; may chat, change the avatar hat and connect Google Calendar. |
| **Operator / developer** | Configures API keys, OAuth client, ports and deployment; runs tests. |

### 2.3 Operating Environment

- Backend: Python ≥ 3.11 (CI uses 3.12), dependencies pinned by `uv.lock`.
- Frontend: Node ≥ 20.19 (CI uses 22), React 19, Vite 7, React Router 7.
- A modern browser with JavaScript, cookies and (optionally) the Geolocation API.
- Optional Docker Compose deployment (`docker-compose.yml`).

### 2.4 Design and Implementation Constraints

- C-1 The assistant schedules exclusively in the `Australia/Sydney` time zone.
- C-2 The LLM is accessed via the OpenAI SDK (`openai>=2.6,<3`); the model is configurable.
- C-3 Google Calendar access uses the full `https://www.googleapis.com/auth/calendar` scope on the user's `primary` calendar.
- C-4 Google credentials are stored as a single `token.json` file for the whole backend.
- C-5 User accounts are stored in a local SQLite database.

### 2.5 Assumptions and Dependencies

- A valid `OPENAI_API_KEY` is available; without it the chat cannot function.
- A Google Cloud project with the Calendar API enabled, a *Web application* OAuth client, a registered redirect URI and the user listed as a test user.
- Public, keyless services (Overpass, Open-Meteo, Nominatim, ip-api) are reachable.

---

## 3. Functional Requirements

Priority: **M** = Must (core, enforced in code or tested), **S** = Should, **C** = Could.

### 3.1 User Account Management (`FR-AUTH`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-AUTH-01 | The system shall let a visitor register with a name, email and password via `POST /api/auth/register`, returning `201` and the public user record `{id, name, email}`. | M | `app.py`, `users.py` |
| FR-AUTH-02 | Registration shall reject an empty name, an email not matching `^[^@\s]+@[^@\s]+\.[^@\s]+$`, or a password shorter than 6 characters, with HTTP `400` and a readable message. | M | `users.create_user` |
| FR-AUTH-03 | Emails shall be trimmed, lower-cased and unique (case-insensitive); a duplicate shall return HTTP `409`. | M | `users.py` |
| FR-AUTH-04 | The system shall authenticate a user by email and password via `POST /api/auth/login`, starting a permanent session (7 days) on success and returning `401 "Incorrect email or password."` otherwise. | M | `app.login` |
| FR-AUTH-05 | `GET /api/auth/me` shall return the current user, or `401` if no valid session exists. | M | `app.current_user` |
| FR-AUTH-06 | `POST /api/logout` shall clear the session. The frontend shall ask for confirmation before logging out and clear its cached user even if the backend is unreachable. | M | `app.logout`, `auth.js`, `Home.jsx` |
| FR-AUTH-07 | After successful sign-up the UI shall show a success message and redirect to sign-in after 2 seconds. | S | `Signup.jsx` |
| FR-AUTH-08 | A user who is already signed in and opens `/signin` shall be redirected to `/home`. | S | `Signin.jsx` |
| FR-AUTH-09 | The routes `/home` and `/settings` shall be accessible only to an authenticated user; otherwise the user is redirected to `/signin`. | M | `RequireAuth.jsx`, `main.jsx` |

### 3.2 Google Calendar Connection (`FR-GCAL`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-GCAL-01 | `GET /api/google/status` shall report `{connected: bool}`; a token that has expired but carries a refresh token shall be refreshed transparently and counted as connected. | M | `google_calendar.is_connected` |
| FR-GCAL-02 | `GET /api/google/login` shall start the OAuth 2.0 web flow with offline access and forced consent, storing the `state` and PKCE code verifier in the session. | M | `app.google_login` |
| FR-GCAL-03 | `GET /api/google/oauth2callback` shall reject a callback with no stored state (`400`), otherwise exchange the code, persist credentials to `token.json`, and redirect to `{FRONTEND_URL}/settings?google=connected`. | M | `app.google_oauth2callback` |
| FR-GCAL-04 | `POST /api/google/disconnect` shall delete the stored token and return `{ok: true}`, or `500` with the error. | M | `app.google_disconnect` |
| FR-GCAL-05 | The Settings page shall show a **Connect Google Calendar** button, disabled and labelled *Connected* when the backend reports a connection. | M | `Settings.jsx` |

### 3.3 Conversational Agent (`FR-AGENT`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-AGENT-01 | `POST /api/chat` shall accept `{message, conversation, location}` and return `400` when `message` is missing. | M | `app.chat` |
| FR-AGENT-02 | Each request shall be built from a system prompt (including the current Sydney date and year), the last 6 user/assistant turns of the client-held transcript, an optional location note, and the new message. Tool messages from earlier turns shall not be replayed. | M | `app.chat`, `HISTORY_TURNS` |
| FR-AGENT-03 | The agent shall run a Reason → Act → Observe loop: call the model with the tool schemas; if it returns tool calls, execute every call, append each result as a tool message, and repeat; stop when the model answers in plain text. | M | `agent.run` |
| FR-AGENT-04 | The loop shall be capped at `AGENT_MAX_STEPS` (default 6). On reaching the cap the system shall make one further model call without tools, instructing it to answer from what it has observed, and report `stop_reason: "max_steps"`. | M | `agent.run`; test `test_runaway_loop_is_capped_but_still_answers` |
| FR-AGENT-05 | Every tool call returned by the model shall receive a matching tool message, even when the arguments are malformed JSON, the tool is unknown, or the handler raises. Each such failure shall be returned as an observation, not an HTTP error. | M | `agent.run`, `make_dispatcher`; tests in `test_agent_loop.py` |
| FR-AGENT-06 | If the model's final text is empty or trivial (`"..."`, `"Ok"`, …) the system shall ask the model once more to elaborate. | S | `agent._finalize` |
| FR-AGENT-07 | The response shall contain `reply`, `stop_reason`, a `trace` (per step: step number, thought, tool, arguments, observation truncated to 400 characters) and all UI payload fields produced by tools (`events`, `items`, `cta`, `tips`, `place_name`, `weather`, `places`, `places_category`). | M | `app.chat`, `agent.py` |
| FR-AGENT-08 | The system prompt shall instruct the model to (a) never state results it has not observed, (b) never invent venue names, addresses, hours, prices or event details, (c) decline plainly requests no tool covers, and (d) stay within the calendar / places / weather role. | M | `app._system_prompt` |
| FR-AGENT-09 | Tool selection shall be decided by the model; the request path shall not route by keyword matching. | M | README; test `test_weather_no_longer_needs_a_keyword` |
| FR-AGENT-10 | The model shall be configurable through `OPENAI_MODEL` (default `gpt-4o-mini`), with completions limited to 800 tokens per call. | S | `app.py`, `agent.py` |

### 3.4 Calendar Tools (`FR-CAL`)

#### 3.4.1 Common rules

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-CAL-01 | Any calendar tool invoked while no Google account is connected shall be refused with status `calendar_not_connected` and an instruction to direct the user to Settings. | M | `make_dispatcher`; test `test_a_disconnected_calendar_is_reported_not_crashed` |
| FR-CAL-02 | Time windows shall be expressible as a preset (`today`, `tomorrow`, `this_week`, `next_week` — weeks run Monday to Sunday) or as an explicit `time_min` / `time_max`. An unusable window shall be returned to the model as `missing_input` for correction. | M | `formatting.resolve_window` |
| FR-CAL-03 | All event times supplied by the model shall be interpreted as Sydney wall-clock time. Any UTC offset the model attaches shall be discarded and the correct offset (including daylight saving) applied by `zoneinfo`. | M | `formatting.to_sydney_wall_clock`; `test_event_times.py` |
| FR-CAL-04 | Dates and times shown to the user shall be formatted in Sydney local time (e.g. `Thursday, 24 September 2026`, `11:45 AM – 12:45 PM`). | M | `formatting.py` |

#### 3.4.2 Preview-then-confirm for writes

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-CAL-10 | Every write tool shall accept a `confirm` flag. With `confirm=false` it shall change nothing and return `status: "awaiting_confirmation"` with a preview and an instruction to end the turn. | M | `tool_handlers._confirmation` |
| FR-CAL-11 | A write tool called with `confirm=true` in the same turn in which it produced a preview shall be refused with status `not_confirmed`. Confirmation is only possible after a new user message. | M | `make_dispatcher`; test `test_a_write_cannot_be_confirmed_in_the_turn_that_previewed_it` |
| FR-CAL-12 | Update and delete previews shall return the matched `event_ids` (up to 10); a confirming call that passes `event_ids` shall affect only those events. | M | `handle_update_event`, `handle_delete_event`; test `test_confirm_updates_only_the_previewed_events` |
| FR-CAL-13 | The time normalised for the preview shall be exactly the time written on confirmation. | M | test `test_what_was_previewed_is_what_is_written` |

#### 3.4.3 Individual tools

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-CAL-20 | **find_events** shall list events in the primary calendar within the window, ordered by start time (default up to 50), returning `id`, `title`, `start`, `end`, `location` and a one-line summary, and expose them as UI payload `events`. | M | `handle_find_events` |
| FR-CAL-21 | **create_event** shall accept a batch of events each with required `summary`, `start_time`, `end_time` and optional `description`, `location`, `attendees` (emails), `recurrence` (RRULE strings) and `reminders` (`{method, minutes}`). Events missing a required field shall be reported by index as `missing_input`. | M | `handle_create_event`, `tools.py` |
| FR-CAL-22 | A single-event create preview shall include a UI card (`items`: title, date, time) and the prompt `cta: "add this now?"`. | S | `handle_create_event` |
| FR-CAL-23 | If the model confirms without resending the events, the system shall create the batch stored in the session when the preview was made. | S | `session["pending_creates"]` |
| FR-CAL-24 | Creation shall report `created` or, when some events fail, `partial` with per-event errors. | M | `handle_create_event` |
| FR-CAL-25 | **update_event** shall find events whose title contains any of the query's keywords (split on `,`, `&`, `and`), within the given window or ±30 days by default, and apply at least one of `summary`, `description`, `location`, `start_time`, `end_time`. A request with no changes shall be `missing_input`. | M | `handle_update_event`, `_match_titles` |
| FR-CAL-26 | **delete_event** shall find events by the same keyword rule and window, preview them noting that deletion cannot be undone, and on confirmation delete them, reporting `deleted` or `partial`. | M | `handle_delete_event` |
| FR-CAL-27 | Update and delete with no matching events shall return `status: "no_match"` with the query and window. | M | handlers |

#### 3.4.4 Clash detection

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-CAL-30 | Every create preview shall carry a `clashes` list computed by the backend from the events on the same day(s). | M | `_clashes_with`; `test_clash_detection.py` |
| FR-CAL-31 | Two events clash only if `other.start < new.end` **and** `other.end > new.start`; events that merely touch at a boundary do not clash. | M | `_clashes_with` |
| FR-CAL-32 | All-day events (no time component) shall be excluded from clash detection. | M | test `test_an_all_day_event_has_no_times_to_compare_and_is_skipped` |
| FR-CAL-33 | A clash shall be reported, never enforced: the preview shall still be shown and the user shall still decide. | M | test `test_a_clash_is_not_a_veto` |
| FR-CAL-34 | If the clash check itself fails, the preview shall still be returned with a note that the slot could not be verified. | S | `handle_create_event` |

### 3.5 Weather (`FR-WX`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-WX-01 | **get_weather** shall use the browser-granted coordinates; failing that, an estimate from the client's public IP via ip-api.com. Private/loopback IPs shall not be geolocated. | M | `_resolve_location`, `weather.get_client_ip` |
| FR-WX-02 | With no location available, the tool shall return `location_unavailable` instructing the model to ask the user rather than guess. | M | `_location_unavailable`; test `test_weather_without_a_location_is_an_observation` |
| FR-WX-03 | The system shall query OpenWeatherMap when `OPENWEATHER_API_KEY` is set, otherwise (or on failure) Open-Meteo. If both fail, an error observation shall be returned. | M | `weather.fetch_weather` |
| FR-WX-04 | The result shall include place name (provider name or Nominatim reverse geocode, else "your area"), temperature, feels-like, humidity, wind and conditions, where the provider supplies them. | M | `weather.current_conditions` |
| FR-WX-05 | The system shall generate rule-based clothing/activity tips from temperature bands (<5, <10, <20, >28, >32 °C), wind ≥ 10 m/s, and rain/snow indicators. | S | `weather.build_weather_tips` |
| FR-WX-06 | The UI shall render a "Today's Tips" card with the available metrics and tips. | S | `Home.jsx` `TipsCard` |

### 3.6 Nearby Places (`FR-PLC`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-PLC-01 | **find_places** shall search OpenStreetMap via Overpass for one of 11 categories: bank, bar, cafe, gym, hospital, library, nightclub, park, pharmacy, restaurant, supermarket — each mapped to one or more OSM tags (e.g. *bar* includes pubs and beer gardens). | M | `places.CATEGORY_TAGS` |
| FR-PLC-02 | Radius shall default to 1500 m, clamped to 100–5000 m; result count shall default to 8, clamped to 1–20. An optional keyword shall filter by name. | M | `places.find_nearby` |
| FR-PLC-03 | Results shall exclude unnamed places, be sorted by great-circle distance, be de-duplicated by name, and contain `name`, `kind`, `distance_m`, `address`, `opening_hours`, `website`. | M | `places.find_nearby` |
| FR-PLC-04 | Fields OSM does not record shall be returned as `null`, and the model instructed to report them as "not listed". The UI shall display "Address not listed" for a missing address. | M | `handle_find_places`, `PlacesCard` |
| FR-PLC-05 | Unknown category → `missing_input` with valid categories; no results → `no_results` with a suggestion to widen the radius; Overpass failure → error observation. | M | `test_places_tool.py` |
| FR-PLC-06 | The model shall be instructed to mention at most two places in its reply and point to the card; the UI shall render all results in a "Nearby" card with distance (m / km) and website link. | S | `handle_find_places`, `PlacesCard` |

### 3.7 Chat User Interface (`FR-UI`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-UI-01 | The landing page (`/`) shall present the product and offer Sign In / Sign Up. | S | `Landing.jsx` |
| FR-UI-02 | The home page shall show a greeting, a scrolling message list that auto-scrolls to the latest message, and a text input that ignores blank submissions. | M | `Home.jsx` |
| FR-UI-03 | The client shall keep the transcript in memory and send it with each message, together with the current coordinates if known. | M | `Home.jsx` |
| FR-UI-04 | Assistant messages shall render reply text (markdown bold stripped), key/value items, a confirmation prompt (`cta`), and weather and places cards when present. | M | `Home.jsx` |
| FR-UI-05 | On load, the client shall request browser geolocation; if permission is denied it shall fall back to an IP-based location (ipapi.co) and tell the user it is approximate. Location errors shall be shown as a tip. | S | `Home.jsx` |
| FR-UI-06 | Network or server errors shall be shown as an assistant message rather than failing silently. | M | `Home.jsx` |
| FR-UI-07 | The sidebar shall show the DOLMA avatar with the selected hat, the signed-in user's email, Settings and Log out buttons. | S | `Home.jsx` |

### 3.8 Personalisation (`FR-PERS`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-PERS-01 | The user shall choose one of three avatar hats — Classic Counsel, Strategist, Scholar — on the Settings page. | C | `Settings.jsx` |
| FR-PERS-02 | The choice shall persist in browser `localStorage`, be applied immediately to the chat sidebar (same tab via a custom event, other tabs via the `storage` event), and be confirmed with a transient toast. | C | `Settings.jsx`, `Home.jsx` |
| FR-PERS-03 | Unknown or legacy hat values shall fall back to Classic. | C | `readStoredHat`, `normalizeHat` |

### 3.9 Operations (`FR-OPS`)

| ID | Requirement | Pri | Source |
|---|---|---|---|
| FR-OPS-01 | `GET /api/health` shall return `{ok: true}`. | S | `app.health` |
| FR-OPS-02 | The system shall be runnable locally (`uv run app.py`, `npm run dev`) and via `docker compose up --build`. | M | README, `docker-compose.yml` |

---

## 4. External Interface Requirements

### 4.1 User Interfaces

Five client routes: `/` (landing), `/signin`, `/signup`, `/home` (chat), `/settings`.
The chat view has a left sidebar (avatar, user, settings, log out) and a chat pane
(messages, cards, location tip, input bar).

### 4.2 REST API

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| POST | `/api/auth/register` | Create account | 201 `{user}` | 400, 409 |
| POST | `/api/auth/login` | Start session | 200 `{user}` | 401 |
| GET | `/api/auth/me` | Current user | 200 `{user}` | 401 |
| POST | `/api/logout` | End session | 200 `{ok}` | — |
| GET | `/api/google/status` | Calendar connected? | 200 `{connected}` | — |
| GET | `/api/google/login` | Start OAuth | 302 to Google | — |
| GET | `/api/google/oauth2callback` | Finish OAuth | 302 to Settings | 400 |
| POST | `/api/google/disconnect` | Remove token | 200 `{ok}` | 500 |
| POST | `/api/chat` | Run the agent | 200 `{reply, trace, stop_reason, …ui}` | 400, 500 |
| GET | `/api/health` | Liveness | 200 `{ok}` | — |

### 4.3 Software Interfaces

| Service | Use | Auth | Timeout |
|---|---|---|---|
| OpenAI Chat Completions | Reasoning and tool selection | API key | SDK default |
| Google Calendar API v3 | Read/write the primary calendar | OAuth 2.0 + PKCE | SDK default |
| Overpass API | Nearby places | none (User-Agent sent) | 30 s |
| OpenWeatherMap | Current weather | API key (optional) | 8 s |
| Open-Meteo | Weather fallback | none | 8 s |
| Nominatim | Reverse geocoding | none (User-Agent sent) | 6 s |
| ip-api.com | IP geolocation (backend) | none | 5 s |
| ipapi.co | IP geolocation (frontend fallback) | none | browser default |

### 4.4 Configuration Interface

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | LLM access |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used by the agent |
| `AGENT_MAX_STEPS` | `6` | Tool rounds per turn |
| `OPENWEATHER_API_KEY` | — | Enables OpenWeatherMap |
| `FLASK_SECRET_KEY` | `dev-secret-change-me` | Session signing |
| `PORT` / `PUBLIC_PORT` | `5000` / `PORT` | Listen port / browser-facing port |
| `FRONTEND_URL` | `http://localhost:5173` | Post-OAuth redirect, CORS |
| `CORS_ALLOW_ORIGINS` | derived | Explicit CORS allow-list |
| `GOOGLE_CLIENT_SECRETS_FILE` | `backend/credentials.json` | OAuth client |
| `GOOGLE_REDIRECT_URI` | `http://localhost:{PUBLIC_PORT}/api/google/oauth2callback` | OAuth callback |
| `TOKEN_FILE` | `backend/token.json` | Stored Google credentials |
| `USERS_DB_PATH` | `backend/users.db` | SQLite database |
| `USER_TIMEZONE` | `Australia/Sydney` | Default TZ in calendar module |
| `VITE_API_BASE` (frontend) | `{protocol}//{host}:5000` | Backend URL |

---

## 5. Non-Functional Requirements

### 5.1 Security (`NFR-SEC`)

| ID | Requirement | Source |
|---|---|---|
| NFR-SEC-01 | Passwords shall be stored only as salted hashes (Werkzeug `generate_password_hash`) and never returned by the API. | `users.py` |
| NFR-SEC-02 | All SQL shall use parameterised queries. | `users.py` |
| NFR-SEC-03 | Login failure shall not disclose whether the email exists ("Incorrect email or password."). | `app.login` |
| NFR-SEC-04 | CORS shall use an explicit origin allow-list with credentials; wildcard origins shall not be used. | `app._cors_origins` |
| NFR-SEC-05 | The OAuth flow shall use `state` and PKCE. Plain-HTTP redirect URIs shall be accepted only for loopback hosts (`localhost`, `127.0.0.1`, `::1`); hosts merely containing "localhost" shall not qualify. | `app._is_loopback_http`; `test_oauth_transport.py` |
| NFR-SEC-06 | Secrets (`.env`, `credentials.json`, `token.json`) shall be excluded from version control and Docker images. | `.gitignore`, `.dockerignore` |
| NFR-SEC-07 | `FLASK_SECRET_KEY` shall be set to a strong value outside local development. | README |

### 5.2 Safety and Trustworthiness of the Agent (`NFR-SAFE`)

| ID | Requirement | Source |
|---|---|---|
| NFR-SAFE-01 | No calendar change shall occur without an explicit user approval given in a later message than the preview (enforced in code, not only by prompt). | FR-CAL-10/11 |
| NFR-SAFE-02 | Changes shall be scoped to the events the user was shown. | FR-CAL-12 |
| NFR-SAFE-03 | Facts presented to the user (venues, addresses, events, weather) shall originate from tool observations in the current turn; missing data shall be reported as missing. | FR-AGENT-08, FR-PLC-04 |
| NFR-SAFE-04 | Deterministic computations — UTC offsets and event overlap — shall be performed by code, not by the language model. | FR-CAL-03, FR-CAL-31 |
| NFR-SAFE-05 | The model shall never see UI-only payloads, so rendered cards cannot be garbled by it. | `ToolResult.ui` |

### 5.3 Reliability and Fault Tolerance (`NFR-REL`)

| ID | Requirement | Source |
|---|---|---|
| NFR-REL-01 | A tool failure, malformed argument or unknown tool shall not fail the request; it shall become an observation the model can act on. Only an OpenAI API failure may produce HTTP 500. | `agent.run`, `app.chat` |
| NFR-REL-02 | Weather shall degrade from OpenWeatherMap to Open-Meteo, and location from browser geolocation to IP estimate. | `weather.py` |
| NFR-REL-03 | Batch writes shall report partial success per event rather than failing as a whole. | handlers |
| NFR-REL-04 | Expired Google access tokens shall be refreshed automatically when a refresh token exists. | `google_calendar.py` |

### 5.4 Performance and Cost (`NFR-PERF`)

| ID | Requirement | Source |
|---|---|---|
| NFR-PERF-01 | A single chat turn shall make at most `AGENT_MAX_STEPS + 2` model calls (loop, forced answer, one elaboration retry). | `agent.py` |
| NFR-PERF-02 | Each model call shall be limited to 800 completion tokens; prompt history shall be limited to 6 turns. | `agent.py`, `app.py` |
| NFR-PERF-03 | Every outbound HTTP call to third-party services shall have a bounded timeout (5–30 s, see §4.3). | `weather.py`, `places.py` |
| NFR-PERF-04 | Calendar look-ups shall be bounded (≤ 50 results for listing, ≤ 100 for matching/clash checks); previews shall show at most 10 events. | handlers |

### 5.5 Usability (`NFR-USE`)

| ID | Requirement | Source |
|---|---|---|
| NFR-USE-01 | Users shall interact in free natural language; no command syntax is required. | FR-AGENT-09 |
| NFR-USE-02 | Dates shall always include the year and be shown in a readable Sydney-local format. | system prompt, `formatting.py` |
| NFR-USE-03 | Error messages shown to users shall be plain sentences (e.g. "Cannot reach the server. Please try again later."). | `auth.js`, `Home.jsx` |
| NFR-USE-04 | Information shown in a card shall not be duplicated at length in the reply text. | `handle_find_places`, `_confirmation` |

### 5.6 Maintainability (`NFR-MAINT`)

| ID | Requirement | Source |
|---|---|---|
| NFR-MAINT-01 | The agent loop shall be domain-agnostic; domain logic lives in tool handlers, tool schemas in a separate module, external APIs in their own modules. | backend layout |
| NFR-MAINT-02 | Adding a tool shall require only a schema in `tools.py` and a handler registered in `HANDLERS`. | `tool_handlers.py` |
| NFR-MAINT-03 | Direct dependencies shall be capped below the next major version and fully pinned in `uv.lock` / `package-lock.json`. | `pyproject.toml` |

### 5.7 Testability and Quality Assurance (`NFR-TEST`)

| ID | Requirement | Source |
|---|---|---|
| NFR-TEST-01 | The backend test suite shall run offline and free of cost, replacing the model and every external service with fakes. | `tests/fakes.py`, `conftest.py` |
| NFR-TEST-02 | Tests shall be runnable with `pytest` and also without any framework (`python tests/run_tests.py`). | `tests/run_tests.py` |
| NFR-TEST-03 | Time-dependent tests shall pin the clock so they stay deterministic. | commit `f849610` |
| NFR-TEST-04 | CI shall run backend tests and the frontend production build on every push to `main` and every pull request. | `.github/workflows/ci.yml` |

### 5.8 Portability and Deployability (`NFR-PORT`)

| ID | Requirement | Source |
|---|---|---|
| NFR-PORT-01 | The backend shall run on Linux, macOS and Windows (e.g. no glibc-only `strftime` flags). | `formatting._fmt` |
| NFR-PORT-02 | The system shall be deployable with Docker Compose, with secrets supplied at runtime rather than baked into images. | `docker-compose.yml`, README |
| NFR-PORT-03 | Ports and URLs shall be configurable so the OAuth redirect URI matches the browser-facing port (5000 local, 5050 Docker). | `app.py` |

### 5.9 Privacy (`NFR-PRIV`)

| ID | Requirement | Source |
|---|---|---|
| NFR-PRIV-01 | Location shall be used only with browser permission; otherwise an approximate IP-based estimate is used and the user is told. | `Home.jsx` |
| NFR-PRIV-02 | The chat transcript shall not be stored on the server; it is held by the browser and only the last 6 turns are forwarded. | `app.chat`, `Home.jsx` |
| NFR-PRIV-03 | User messages, recent history and calendar data needed for a turn are sent to OpenAI; users should be informed of this. | architecture |

---

## 6. Use Cases (summary)

| UC | Actor | Main flow | Requirements |
|---|---|---|---|
| UC-1 Register & sign in | Visitor | Sign up → redirected to sign in → chat | FR-AUTH-01…09 |
| UC-2 Connect calendar | User | Settings → Connect → Google consent → back to Settings, "Connected" | FR-GCAL-01…05 |
| UC-3 Ask about schedule | User | "What's on tomorrow?" → `find_events` → reply | FR-CAL-20 |
| UC-4 Add an event | User | "Add lunch Thu 11:45" → `create_event(confirm=false)` → preview + clashes → user "yes" → `create_event(confirm=true)` | FR-CAL-10…13, 21…24, 30…34 |
| UC-5 Reschedule / delete | User | Request → preview with `event_ids` → user approves → write only those events | FR-CAL-12, 25…27 |
| UC-6 Weather-aware advice | User | "Should I run outside today?" → `get_weather` → `find_events` → combined answer | FR-WX-*, FR-AGENT-03 |
| UC-7 Find a place | User | "Any cafés nearby?" → `find_places` → short reply + card | FR-PLC-* |
| UC-8 Out-of-scope request | User | "Book me a table" → plain statement that DOLMA cannot do it | FR-AGENT-08 |

---

## Appendix A — Requirement-to-Test Traceability (selected)

| Requirement | Test file |
|---|---|
| FR-AGENT-03/04/05 | `test_agent_loop.py` |
| FR-CAL-01, 10–13, 20–26 | `test_calendar_tools.py` |
| FR-CAL-30–34 | `test_clash_detection.py` |
| FR-CAL-03, 13 | `test_event_times.py` |
| FR-PLC-01–06, NFR-SAFE-03 | `test_places_tool.py` |
| NFR-SEC-05 | `test_oauth_transport.py` |

Not covered by automated tests: user accounts (`FR-AUTH`), OAuth endpoints, weather
provider fallback, and the whole frontend (CI only checks that it builds).

## Appendix B — Observed Gaps and Candidate Requirements

Found while deriving the specification. Each one is a difference between what the
system *implies* and what the code *does*, and a candidate for a future requirement.

| # | Observation | Candidate requirement |
|---|---|---|
| B-1 | `/api/chat` and `/api/google/*` do not check for a signed-in session; only the frontend routes are guarded. | The backend shall reject unauthenticated calls to chat and calendar endpoints with 401. |
| B-2 | Google credentials are one global `token.json`, shared by every DOLMA user. | Google credentials shall be stored per user account. |
| B-3 | `FLASK_SECRET_KEY` silently falls back to a hard-coded default. | The backend shall refuse to start outside development without a secret key. |
| B-4 | No rate limiting or login throttling. | Login and chat endpoints shall be rate-limited per user/IP. |
| B-5 | `/api/google/disconnect` exists but the Settings page has no Disconnect button. | The Settings page shall let the user disconnect Google Calendar. |
| B-6 | The OAuth callback lets exceptions from `fetch_token` (e.g. `access_denied`) surface as a 500. | A failed or cancelled OAuth flow shall redirect to Settings with an error message. |
| B-7 | The transcript is lost on page reload. | (Optional) Conversation history shall persist per user. |
| B-8 | The `cta` text ("add this now?") is display-only; the user must type a reply to confirm. | (Optional) The UI shall offer Confirm / Cancel buttons. |
| B-9 | Scheduling is fixed to `Australia/Sydney`. | (Optional) Each user shall have a configurable time zone. |
| B-10 | Two hat stores coexist: `DolmaAvatarContext` (`dolma:selected-hat`) and the pages (`dolmaHat`); `App.jsx` is unused Vite boilerplate. | Avatar state shall have a single source of truth; dead code removed. |
| B-11 | The frontend's IP fallback uses ipapi.co while the backend uses ip-api.com. | A single geolocation provider shall be used. |
