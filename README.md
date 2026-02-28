# DemandEngine

Autonomous AI agent that earns revenue in the [Virtuals Protocol](https://virtuals.io) ACP marketplace. It monitors bounty boards, classifies incoming tasks, claims viable jobs, fulfills them with Claude, and delivers results — entirely without human intervention.

---

## How It Works

```
agdp.io bounty board          ACP marketplace buyers
        │                              │
        ▼                              ▼
  AGDPBountyMonitor          ACP Seller Runtime (Node.js)
        │                              │
        └──────────┬───────────────────┘
                   ▼
           TaskClassifier
         (category + feasibility)
                   │
           feasibility ≥ 0.4?
                   │
                   ▼
              Orchestrator
         (decompose → parallel
          Claude calls → assemble)
                   │
                   ▼
            AGDPDelivery / ACP
```

1. **Intake** — two parallel sources feed jobs in:
   - `AGDPBountyMonitor` polls `agdp.io` every 60 s for human-posted bounties
   - ACP Seller Runtime (TypeScript) receives socket events from the ACP marketplace for direct buyer requests

2. **Classify** — `TaskClassifier` calls Claude to assign a category and feasibility score. Low-feasibility tasks (trading with real capital, illegal requests) are skipped before any claim is made.

3. **Claim** — viable bounties are claimed via `bounty.virtuals.io/api/v1`.

4. **Execute** — `Orchestrator` decomposes complex requests into 2–5 parallel sub-tasks, fulfills each with a category-specific Claude prompt, then assembles results into a polished deliverable.

5. **Deliver** — results are posted back via the ACP delivery API. Job status is tracked in SQLite.

6. **Notify** — operator receives Telegram notifications for every claimed job and completion.

---

## Services

| Service | Runtime | Port | Purpose |
|---------|---------|------|---------|
| `demand-engine` | Python / FastAPI | 8080 | Core engine — intake, classification, execution, delivery |
| `acp-seller` | Node.js / TypeScript | — | ACP socket runtime — receives marketplace job events |

---

## ACP Offerings

Nine offerings are registered in the ACP marketplace:

| Offering | Price | SLA |
|----------|-------|-----|
| `code_development` | $5 | 45 min |
| `audit_review` | $5 | 60 min |
| `technical_writing` | $4 | 45 min |
| `data_analysis` | $4 | 45 min |
| `planning` | $4 | 45 min |
| `content_creation` | $3 | 30 min |
| `general_task` | $3 | 60 min |
| `summarization` | $2 | 20 min |
| `quick_answer` | $0.01 | 5 min |

Re-register offerings after changes:
```bash
python register_offerings.py
```

---

## Project Structure

```
demand-engine/
├── src/
│   ├── intake/
│   │   ├── agdp_bounty_monitor.py   # Polls agdp.io, claims bounties
│   │   ├── telegram_bot.py          # Operator control & notifications
│   │   └── twitter_intake.py        # Optional Twitter DM intake
│   ├── intelligence/
│   │   ├── task_classifier.py       # Claude-powered category + feasibility
│   │   ├── agent_matcher.py         # Finds ACP agents for sub-tasks
│   │   ├── pricing_engine.py        # Dynamic pricing with margin
│   │   └── competitor_monitor.py    # Tracks top ACP agents hourly
│   ├── execution/
│   │   ├── orchestrator.py          # Decompose → parallel → assemble
│   │   ├── direct_fulfillment.py    # Claude category-specific prompts
│   │   ├── agdp_delivery.py         # Posts results to ACP
│   │   ├── acp_client.py            # ACP REST client
│   │   ├── payment.py               # Basescan payment detection
│   │   └── job_manager.py           # Job lifecycle management
│   ├── data/
│   │   ├── models.py                # SQLAlchemy models (Job, Revenue)
│   │   └── tracker.py               # Revenue and stats tracking
│   ├── web/
│   │   └── dashboard.py             # FastAPI dashboard endpoints
│   ├── config.py                    # All env vars in one place
│   ├── notifications.py             # Telegram operator alerts
│   └── main.py                      # Entry point, wires everything together
├── seller-offerings/                # ACP offering handlers (TypeScript)
│   ├── ai_content_creation/
│   ├── ai_research_analysis/
│   └── task_orchestration/
├── register_offerings.py            # One-shot ACP offering registration
├── check_jobs.py                    # CLI to inspect job database
├── Dockerfile
├── start.sh                         # Starts both Python + Node services
└── requirements.txt
```

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- A [Virtuals Protocol](https://virtuals.io) agent account with an API key

### 1. Clone and install

```bash
git clone <repo>
cd demand-engine
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in all values:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
ACP_AGENT_WALLET=0x...          # Your agent's on-chain wallet address
LITE_AGENT_API_KEY=acp-...      # Virtuals Protocol API key

# Optional — enables operator notifications
OPERATOR_CHAT_ID=...            # Your Telegram chat ID (/myid in bot)

# Optional — enables Twitter DM intake
TWITTER_BEARER_TOKEN=...

# Optional — improves payment detection reliability
BASESCAN_API_KEY=...

# Defaults
CLAUDE_MODEL=claude-sonnet-4-6
DATABASE_URL=sqlite+aiosqlite:///demand_engine.db
MIN_JOB_PRICE=5.0
MAX_JOB_PRICE=500.0
DEFAULT_MARGIN_PERCENT=60
```

### 3. Set up ACP Seller Runtime

```bash
git clone https://github.com/Virtual-Protocol/openclaw-acp.git openclaw-acp
cd openclaw-acp && npm install
```

Copy your ACP `config.json` into `openclaw-acp/config.json`.

### 4. Register offerings

```bash
python register_offerings.py
```

### 5. Run

```bash
# Python engine
python -m src.main

# ACP seller runtime (separate terminal)
cd openclaw-acp && npx tsx src/seller/runtime/seller.ts
```

---

## Deployment (Systemd)

Two systemd services run on the VPS:

```ini
# /etc/systemd/system/demand-engine.service
[Unit]
Description=DemandEngine - ACP Bounty Automation
After=network.target

[Service]
WorkingDirectory=/opt/demand-engine
EnvironmentFile=/opt/demand-engine/.env
ExecStartPre=-/bin/fuser -k 8080/tcp
ExecStart=/opt/demand-engine/venv/bin/python -m src.main
Restart=on-failure
RestartSec=10
```

```ini
# /etc/systemd/system/acp-seller.service
[Unit]
Description=DemandEngine ACP Seller Runtime
After=network.target demand-engine.service

[Service]
WorkingDirectory=/opt/demand-engine/openclaw-acp
EnvironmentFile=/opt/demand-engine/.env
ExecStart=/usr/bin/node node_modules/.bin/tsx src/seller/runtime/seller.ts
Restart=on-failure
RestartSec=10
```

```bash
systemctl restart demand-engine acp-seller
systemctl status demand-engine acp-seller
journalctl -u demand-engine -f
```

### Docker

```bash
docker build -t demand-engine .
docker run -d --env-file .env -p 8080:8080 demand-engine
```

---

## Telegram Operator Commands

Send these to your bot:

| Command | Description |
|---------|-------------|
| `/status` | Running services, jobs today, revenue |
| `/jobs` | Recent job history |
| `/revenue` | Earnings breakdown |
| `/topage` | Live ACP competitor leaderboard |

---

## Task Classification

The classifier uses Claude to assign each incoming request a **category** and **feasibility score** (0.0–1.0). Jobs are only claimed when feasibility meets the threshold:

- General tasks: ≥ 0.4
- Trading/on-chain execution: ≥ 0.55 (and skipped entirely if they require real capital)
- Research-only: always skipped

Categories: `content_creation`, `copywriting`, `technical_writing`, `code`, `qa_testing`, `audit_review`, `data_analysis`, `social_media`, `trading`, `design`, `summarization`, `translation`, `planning`, `product`, `other`

---

## Bounty Flow (agdp.io)

1. Fetch bounties from `https://agdp.io/api/bounties`
2. Filter: budget $0.04–$100
3. Classify → skip if feasibility too low or category is research
4. Claim via `POST https://bounty.virtuals.io/api/v1/bounties/{id}/claim`
5. Fulfill via Orchestrator (Claude)
6. Deliver via ACP delivery API
7. Callback endpoint (`/agdp-callback/{job_id}`) marks job complete on confirmation

---

## Tests

```bash
pytest tests/ -v
```
