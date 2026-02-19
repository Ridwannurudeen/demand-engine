# aGDP.io Bounty Monitor Setup

The demand-engine now automatically monitors and claims bounties from https://agdp.io/bounties!

## What Was Added

### 1. **AGDPBountyMonitor** (`src/intake/agdp_bounty_monitor.py`)
- Polls https://agdp.io/api/bounties every 60 seconds
- Automatically classifies new bounties
- Claims bounties that match our capabilities
- Executes jobs via ACP or direct fulfillment

### 2. **Integration** (`src/main.py`)
- Bounty monitor starts automatically on service boot
- Runs alongside Telegram bot and Twitter intake
- Graceful shutdown on service stop

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│  aGDP.io Bounty Monitor (every 60s)                     │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Fetch new bounties with status: pending_match          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  For each new bounty:                                   │
│  1. Classify task (content, research, code, etc.)       │
│  2. Check if we can fulfill (confidence, budget, agents)│
│  3. Create Job in database                              │
│  4. Claim bounty on aGDP.io                             │
│  5. Execute job via Orchestrator                        │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Job execution (ACP or Direct Fulfillment)              │
│  → Deliverable sent to aGDP.io                          │
│  → Revenue tracked in database                          │
└─────────────────────────────────────────────────────────┘
```

## Filtering Criteria

Bounties are claimed if:
- ✅ Classification confidence >= 60%
- ✅ Budget between $0.50 - $100 USDC
- ✅ We have matching ACP agents OR category is simple (content, research, code, data_analysis)
- ✅ Status is `pending_match`

Bounties are skipped if:
- ❌ Too low confidence (<60%)
- ❌ Budget too low (<$0.50) or too high (>$100)
- ❌ No matching agents and complex category
- ❌ Already claimed by someone else

## Deployment to VPS

### Option 1: Restart existing service
```bash
ssh user@38.49.212.108
cd /path/to/demand-engine
git pull origin main
sudo systemctl restart demand-engine
# OR
pm2 restart demand-engine
```

### Option 2: Manual deployment
```bash
# On your VPS (38.49.212.108)
cd /path/to/demand-engine
git pull origin main
pkill -f "python.*main.py"  # Stop old process
nohup python3 src/main.py > demand-engine.log 2>&1 &
```

## Monitoring

### Check logs
```bash
# If using systemd
sudo journalctl -u demand-engine -f

# If using pm2
pm2 logs demand-engine

# If using nohup
tail -f demand-engine.log
```

### Check dashboard
Visit: http://38.49.212.108:8080/dashboard

You'll see:
- Total jobs (including claimed bounties)
- Revenue from completed jobs
- Recent job history with aGDP bounty IDs

### Check API
```bash
curl http://38.49.212.108:8080/
# Returns: {"status": "ok", "service": "demand-engine"}
```

## Testing Locally

```bash
cd /home/node/.openclaw/workspace/demand-engine

# Install dependencies
pip3 install -r requirements.txt

# Create .env file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
TELEGRAM_BOT_TOKEN=your-token-here
ACP_AGENT_WALLET=0xYourWalletAddress
EOF

# Run
python3 src/main.py
```

Watch the logs for:
```
[INFO] aGDP.io Bounty Monitor started (polling every 60s)
[INFO] Found 2 new bounties to process
[INFO] Processing bounty #51: Buy 5-minute AI Recovery Session...
[INFO] Bounty #51 → Job #3 created (category: content_creation, price: 1.10 USDC)
[INFO] Successfully claimed bounty #51
[INFO] Bounty #51 (Job #3) execution started
```

## Current Live Bounties (as of Feb 19, 05:44 UTC)

1. **AI Therapy Session** - 1.10 USDC (#51)
2. **Delx Therapy (Non-Self)** - 1.10 USDC (#50)
3. **Heartbeat Retention Experiment** - 0.01 USDC (#47)
4. **3D Gatcha Card Pack** - 50 USDC (#43)
5. **Test bounties** - 1.00 USDC each (#44, #45, #46)

## Troubleshooting

### Bounty monitor not starting
- Check logs for errors
- Verify ANTHROPIC_API_KEY is set
- Verify ACP_AGENT_WALLET is set

### Bounties not being claimed
- Check classification confidence (might be <60%)
- Check budget range ($0.50 - $100)
- Verify agent matcher is finding suitable agents
- Check aGDP.io API status

### Jobs failing to execute
- Check ACP integration (openclaw-acp directory)
- Verify direct fulfillment is working
- Check orchestrator logs

## Next Steps

1. **Deploy to VPS** - Push changes and restart service
2. **Monitor first claim** - Watch logs for bounty #51 or #50
3. **Verify deliverable** - Check aGDP.io for job completion
4. **Track revenue** - View dashboard for earnings

## Configuration

Edit `src/intake/agdp_bounty_monitor.py` to adjust:
- `POLL_INTERVAL` (default: 60 seconds)
- Minimum confidence threshold (default: 0.6)
- Budget range (default: $0.50 - $100)
- Agent matching criteria
