# SmartFacility Monitor

SmartFacility Monitor is a Flask application for ingesting building and
industrial sensor readings, detecting threshold excursions and gradual drift,
and presenting actionable safety alerts to operators.

## Architecture

```mermaid
flowchart LR
    SIM[Sensor simulator] -->|API key| API[Flask API blueprint]
    DEV[IoT devices] -->|HTTPS / JSON| API
    API --> RULES[Threshold and drift rules]
    RULES --> DB[(SQLite or MySQL)]
    API --> DB
    WEB[Browser] --> AUTH[Login and CSRF]
    WEB --> DASH[Dashboard blueprint]
    AUTH --> DB
    DASH --> DB
    DASH --> CHART[Matplotlib PNG chart]
    FACTORY[Flask app factory] --> API
    FACTORY --> AUTH
    FACTORY --> DASH
```

The application uses a factory and three blueprints: `auth`, `api`, and
`dashboard`. SQLAlchemy models hold users, sensors, readings, and alerts.
Threshold and drift calculations live in `app/rules.py`; the calculation
helpers are pure functions and the integration function creates alerts.

## Features

- Five sensor categories with individually configurable warning and critical
  bounds.
- JSON reading ingestion guarded by an `X-API-Key`, with finite-number and
  sensor-state validation.
- Threshold rules and a predictive drift rule comparing five recent readings
  with the previous twenty; repeated open alerts are suppressed.
- Role-based web access: admins manage sensors and users, engineers/admins
  acknowledge and resolve alerts, and viewers are read-only.
- Flask-Login sessions, Werkzeug password hashes, Flask-WTF CSRF protection,
  first-login password change enforcement, and production configuration checks.
- Responsive Bootstrap 5 pages, ten-second overview refresh, paginated alerts,
  and server-rendered Matplotlib PNG trend charts. No custom JavaScript is used.
- SQLite by default, or MySQL through `DATABASE_URL`; health endpoint at
  `/health`; `flask init-db` and `flask seed` commands.
- Optional hosted demo mode generates sensor readings in a background daemon
  thread and prunes old readings to bound storage.

## Project layout

```text
SmartFacility Monitor/
├── app/
│   ├── __init__.py
│   ├── models.py
│   ├── rules.py
│   ├── forms.py
│   ├── charts.py
│   ├── blueprints/
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   └── dashboard/
│   │       ├── __init__.py
│   │       └── routes.py
│   └── templates/
│       ├── base.html
│       ├── auth/
│       │   ├── login.html
│       │   └── change_password.html
│       ├── dashboard/
│       │   ├── overview.html
│       │   ├── sensor_detail.html
│       │   └── alerts.html
│       └── admin/
│           ├── sensors.html
│           ├── edit_sensor.html
│           ├── users.html
│           ├── create_user.html
│           └── edit_user.html
├── simulator/
│   ├── __init__.py
│   └── sensor_sim.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_rules.py
│   ├── test_api.py
│   └── test_auth.py
├── config.py
├── run.py
├── requirements.txt
├── pytest.ini
├── .env.example
├── .gitignore
└── README.md
```

## Windows setup (PowerShell)

Use Python 3.11 or newer. From the project directory:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` (do not overwrite an existing configured `.env`). Generate two
different secrets:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste one generated value into `SECRET_KEY` and another into `API_KEY`.
`DATABASE_URL=sqlite:///smartfacility.db` is ready for local use. Do not commit
`.env`.

Initialize, seed, and run:

```powershell
$env:FLASK_APP = "run.py"
flask init-db
flask seed
python run.py
```

Open <http://127.0.0.1:5000>. The seeded admin login is `admin` /
`Admin@123`; the first login is forced to change that password.

In another PowerShell window, activate the environment and run the simulator.
It reads `API_KEY` from `.env`:

```powershell
.\venv\Scripts\Activate.ps1
python simulator\sensor_sim.py --interval 5
python simulator\sensor_sim.py --interval 2 --drift
```

Stop the simulator with Ctrl+C. `--spike-rate 0.2` sets a 20% per-reading spike
chance; `--sensor-id 1` restricts simulation to a single sensor.

## Hosted demo

Set these variables in the host's environment to generate readings inside the
Flask app without running `simulator\sensor_sim.py` separately:

```dotenv
DEMO_MODE=true
DEMO_INTERVAL_SECONDS=5
DATA_RETENTION_HOURS=24
```

Demo mode is disabled by default. When enabled, the application starts one
process-wide daemon thread (the Werkzeug reloader parent defers startup to its
child). The thread creates one reading for each active sensor per cycle using
the simulator's sensor-type baselines, noise, occasional spikes, and gradual
drift; each reading goes through the normal rules engine. Every 100 cycles it
deletes readings older than `DATA_RETENTION_HOURS`, while retaining alerts and
clearing references to pruned readings.

On a host that uses multiple worker processes, use one web worker when demo
mode is enabled: thread guards are process-local, so each worker would
otherwise produce its own readings. Initialize the database and seed sample
sensors before enabling demo mode:

```powershell
$env:FLASK_APP = "run.py"
flask init-db
flask seed
```

The demo thread is for previews and hosted demonstrations, not a durable
production scheduler. For multi-worker production deployments, move scheduled
generation to a dedicated worker or task queue.

## Optional MySQL setup

Run these commands in MySQL as an administrator, replacing the example
password with a unique strong password:

```sql
CREATE DATABASE smart_facility
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
CREATE USER 'sfm_user'@'localhost'
  IDENTIFIED BY 'REPLACE_WITH_A_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON smart_facility.* TO 'sfm_user'@'localhost';
FLUSH PRIVILEGES;
```

Set the following in `.env` (URL-encode special characters in the password):

```dotenv
DATABASE_URL=mysql+pymysql://sfm_user:REPLACE_WITH_A_STRONG_PASSWORD@localhost:3306/smart_facility?charset=utf8mb4
```

Then run `flask init-db` and `flask seed` as above.

## REST API

API endpoints are mounted under `/api`. GET routes return JSON and do not
require an API key. Reading ingestion requires `X-API-Key`. Alert changes
require a logged-in Flask session with engineer or admin role; the ingestion
key alone cannot change alerts. Requests that modify web-session state remain
protected by CSRF.

### Ingest a reading

```powershell
curl.exe -X POST http://127.0.0.1:5000/api/readings `
  -H "X-API-Key: YOUR_API_KEY" `
  -H "Content-Type: application/json" `
  -d "{\"sensor_id\":1,\"value\":72.5}"
```

Returns `201 Created` with the reading and any generated alerts. Missing or
invalid keys return `401`; invalid data returns `400`; unknown sensors return
`404`; inactive sensors return `409`.

### List sensors and readings

```powershell
curl.exe http://127.0.0.1:5000/api/sensors
curl.exe "http://127.0.0.1:5000/api/sensors?include_inactive=true"
curl.exe "http://127.0.0.1:5000/api/sensors/1/readings?limit=20"
```

Reading limits must be positive integers and are capped at 1,000.

### List and filter alerts

```powershell
curl.exe "http://127.0.0.1:5000/api/alerts?status=open&severity=critical"
```

Supported `status` values are `open`, `acknowledged`, and `resolved`.
Supported `severity` values are `warning`, `critical`, and `drift`. List limits
are capped at 500.

### Acknowledge or resolve an alert

`PATCH /api/alerts/<id>` accepts `{"action":"acknowledge"}` or
`{"action":"resolve"}`. Authenticate as an engineer/admin using the normal
Flask-Login session and provide the CSRF token as `X-CSRFToken`; the dashboard
provides CSRF-protected buttons for both actions. A shared ingestion API key
is intentionally not sufficient for this privileged operation.

```powershell
curl.exe -X PATCH http://127.0.0.1:5000/api/alerts/5 `
  -b "session=YOUR_ENGINEER_SESSION_COOKIE" `
  -H "X-CSRFToken: YOUR_CSRF_TOKEN" `
  -H "Content-Type: application/json" `
  -d "{\"action\":\"resolve\"}"
```

Obtain the session cookie and CSRF token from an authenticated engineer/admin
browser session. Invalid transitions return `409 Conflict`; missing sessions
return `401` and viewer sessions return `403`.

### Health

```powershell
curl.exe http://127.0.0.1:5000/health
```

Returns `{"status":"ok","version":"1.0.0"}`.

## Tests and coverage

The tests use an in-memory SQLite database and a dedicated test API key:

```powershell
pytest
pytest tests\test_rules.py
pytest tests\test_api.py
```

Coverage configuration is in `pytest.ini`; the test suite targets the rules
engine and API blueprint, including authentication, validation, threshold and
drift boundaries, duplicate suppression, API error responses, and role access.

## GitHub

Create an empty repository on GitHub, then use these commands from the project
directory:

```powershell
git init
git add .
git commit -m "Initial SmartFacility Monitor project"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/smartfacility-monitor.git
git push -u origin main
```

The `.gitignore` excludes `.env`, databases, virtual environments, logs, and
test coverage output. Review `git status` before pushing to ensure no local
secrets have been staged.

## Future improvements

- Extract alert processing into a Spring Boot service and publish sensor
  events through Kafka for asynchronous, high-volume ingestion.
- Add Docker Compose for Flask, MySQL, Kafka, and an alert-service deployment.
- Introduce WebSockets for push-based dashboard updates instead of meta-refresh.
- Add scoped per-device API credentials, MQTT ingestion, and alert delivery
  integrations.
- Use a time-series database and add retention, aggregation, and migration
  strategies for large reading volumes.
