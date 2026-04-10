# Toris Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**A second brain you can talk to out loud.**

<!-- Demo GIF here -->

Toris is a voice-first thinking partner that lives in Telegram. You walk, you talk, it listens — and unlike a chatbot, it actually *does* things. It researches the market while you're still mid-sentence. It pushes back when an idea doesn't hold up. It remembers what you said last week. It writes and runs the code when you're ready to stop thinking and start building.

It's not here to wait for orders. It's here to think *with* you, offload what's in your head, and hand it back when you're ready to act.

And it runs on your **Claude Pro / Max / Teams subscription** — the same OAuth token that powers your `claude` CLI at the terminal. No API credits to top up, no per-token billing, no separate Anthropic bill.

## Why It Exists

Most "AI assistants" are a text box you type at. Fine when you're at a desk. But the most useful thinking — half-formed ideas, "wait, what if…", the stuff you'd say out loud to a smart friend — happens when you're walking, driving, or pacing around the kitchen. Nowhere near a keyboard.

Voice chatbots exist, sure. But they're wrappers around chat completions: they can't read your files, can't search the web, can't write a script and run it, can't remember yesterday. Parrots with good diction.

Toris is built on the Claude Agent SDK, which means real tools — Bash, Read, Grep, WebSearch, Edit, Write — all reachable through a voice message in Telegram. Say *"check if anyone's already built this and if not, scaffold it in the sandbox"* and it will. Say *"remember that"* and it will, via a pluggable MCP memory server. Say nothing for two days and come back to `/continue` where you left off.

## What It's Good At

- **Walking-around thinking** — pace around, dump half-formed ideas, get real push-back instead of validation theater
- **Reality-checking** — searches the web to ground ideas in what actually exists, before you spend a week building the wrong thing
- **Remembering** — notes and threads persist across sessions; the default persona uses [MEGG](https://www.npmjs.com/package/megg), but any MCP memory server works
- **Doing the work** — when you're ready, it writes and executes code in a sandbox instead of just describing it
- **Session continuity** — conversations persist across messages, restarts, and days; navigate them with `/sessions`, `/search`, `/switch`, `/compact`
- **Per-tool approval** — "Approve" mode lets you authorize each action before it runs; "Go All" gets out of the way
- **Multi-persona** — run multiple AI personalities from one codebase, each with its own voice, sandbox, and bot token

## Simpler than OpenClaw, narrower by choice

[OpenClaw](https://github.com/openclaw/openclaw) is a brilliant Swiss army knife — 13+ chat channels (WhatsApp, Slack, Discord, Signal, iMessage, Telegram, Matrix, …), your infra, your keys. If you want every channel under one roof, run that.

Toris is the opposite bet: **one channel (Telegram), one opinion about how voice-first agentic thinking should feel, built directly on top of Claude Code.** That narrowness buys you three things:

- **One command to start.** `docker-compose up` and the bot is live. No web UI to self-host, no multi-channel adapter layer, no YAML to hand-edit. The rest of setup is conversational in Telegram via `/setup`.
- **Your Claude subscription is the only auth.** Paste an OAuth token from `claude setup-token` into `/setup` and you're done. No `ANTHROPIC_API_KEY`, no API credits, no per-token billing. Same token as your `claude` CLI.
- **Native Claude Code primitives, not bolted on.** Tool approval, sandbox isolation, session management, `/compact`, watch mode — these aren't features Toris reimplements. They're what the Claude Agent SDK already does, exposed through voice messages.

If you need many channels and maximum flexibility, go to OpenClaw. If you want Claude Code in your pocket with a voice, stay here.

## Features

| Feature | Description |
|---------|-------------|
| Voice in/out | ElevenLabs or OpenAI — choose per-user via `/settings` |
| Agentic execution | Claude Agent SDK with Bash, Read, Grep, WebSearch, Edit, Write |
| Sandboxed writes | All writes and command execution confined to a sandbox directory |
| Session management | `/new`, `/continue`, `/sessions`, `/switch`, `/search`, `/compact` |
| Approval modes | "Go All" (auto) or "Approve" (confirm each tool call) |
| Watch mode | Stream tool calls live to chat — Off / Live / Debug |
| Automations | List & toggle scheduled tasks via `/automations` |
| Conversational setup | `/setup` walks you through credentials + voice config in chat |
| Token verification | Every credential is tested before it's saved |
| Multi-persona | Run multiple AI personas from one codebase |
| Topic filtering | Multiple personas in one Telegram group (forum topics) |
| Rate limiting | 2s cooldown + 10/min per user |
| Admin-gated setup | `ALLOWED_USER_IDS` + `ADMIN_USER_IDS` for multi-user chats |

## Architecture

```
Telegram (voice / text / photo)
        │
        ▼
┌──────────────────┐
│  python-telegram │
│       -bot       │
└────────┬─────────┘
         │
    ┌────┴────┐
    │   STT   │  ← ElevenLabs Scribe OR OpenAI Whisper (voice only)
    └────┬────┘
         ▼
┌──────────────────┐
│ Claude Agent SDK │  ← Bash, Read, Grep, WebSearch, Edit, Write
│  + tool approval │
│  + session mgmt  │
└────────┬─────────┘
         │
    ┌────┴────┐
    │   TTS   │  ← ElevenLabs OR OpenAI (if audio enabled)
    └────┬────┘
         ▼
     Telegram
```

## Prerequisites

- **Telegram Bot** — Create one via [@BotFather](https://t.me/botfather)
- **Voice provider (optional)** — ElevenLabs *or* OpenAI API key. Can be configured later via `/setup` or skipped for text-only mode.
- **Claude access** — API key or subscription OAuth token. Can be configured via `/setup` in Telegram.

For Docker deployment: **Docker** and **Docker Compose**.
For non-Docker deployment: **Python 3.11+** and **Node.js 20+** (for the Claude Code CLI).

### Claude Authentication

Choose ONE of these methods:

| Method | Best For | How |
|--------|----------|-----|
| **API Key** | Docker, CI/CD, teams | Paste `sk-ant-api-...` from [console.anthropic.com](https://console.anthropic.com) via `/setup`, or set `ANTHROPIC_API_KEY` in env |
| **Subscription OAuth** | Personal use, Pro/Max/Teams plans | Run `claude setup-token` on any machine with a browser, paste the result via `/setup`, or set `CLAUDE_CODE_OAUTH_TOKEN` in env |
| **Mounted credentials** | Docker with existing `claude /login` | Mount `~/.claude/.credentials.json` into the container (see docker-compose.yml) |

## Deployment Options

### Option 1: Docker (Recommended for Production)

```bash
# Clone the repository
git clone --recurse-submodules https://github.com/toruai/toris-agent.git
cd toris-agent

# Configure
cp docker/toris.env.example docker/toris.env
# Edit docker/toris.env — you only need TELEGRAM_BOT_TOKEN to start.
# Everything else (Claude auth, voice provider) can be set via /setup in Telegram.

# Start
docker-compose up -d

# View logs
docker-compose logs -f toris

# Stop
docker-compose down
```

**Benefits:**
- Isolated sandbox for file operations
- Automatic restarts on failure
- Persistent state across restarts (volumes)
- No Python / Node installation on the host

### Option 2: Non-Docker (systemd or foreground)

```bash
# Clone and setup
git clone --recurse-submodules https://github.com/toruai/toris-agent.git
cd toris-agent

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Configure — only TELEGRAM_BOT_TOKEN is needed to start
cp .env.example .env
# Edit .env with your bot token

# Run
python bot.py
```

See [Systemd Deployment](#systemd-deployment) for a production unit file.

---

## First-time setup via Telegram

After starting the bot for the first time, open your Telegram chat and run `/setup`. The bot walks you through a conversational onboarding:

1. **Name** — how the bot should address you
2. **Claude auth** — choose API Key or OAuth (Claude Pro/Max/Teams subscription)
   - **API Key**: paste your `sk-ant-api-...` from [console.anthropic.com](https://console.anthropic.com)
   - **OAuth**: run `claude setup-token` on any machine with a browser, paste the result
3. **Voice provider** — ElevenLabs, OpenAI, or skip (text-only)
4. **Verification** — the bot tests every credential against the live API before saving

Your tokens are deleted from the Telegram chat immediately after being verified and stored. You can re-run `/setup` at any time, or use targeted commands: `/claude_token`, `/elevenlabs_key`, `/openai_key`.

If a message delete fails (missing Telegram permissions), the bot refuses to save the token and asks you to delete it manually — no token ever stays visible in chat.

---

## Configuration

### Required environment variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/botfather) |
| `TELEGRAM_DEFAULT_CHAT_ID` | Your Telegram chat ID (security: only this chat can use the bot; set to `0` during first setup, then lock it down) |

### Auth — configure one or more (or do it via `/setup` in Telegram)

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access via API key |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude via subscription OAuth (Pro/Max/Teams) |
| `ELEVENLABS_API_KEY` | ElevenLabs voice provider |
| `OPENAI_API_KEY` | OpenAI voice provider |

### Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_ALLOWED_USER_IDS` | (empty) | Comma-separated user IDs; empty = all users in chat allowed |
| `TELEGRAM_ADMIN_USER_IDS` | (empty) | Comma-separated admin IDs; empty = all authorized users are admins |
| `TELEGRAM_TOPIC_ID` | (empty) | Restrict bot to a specific Telegram forum topic |
| `PERSONA_NAME` | `Assistant` | Display name in logs / greetings |
| `SYSTEM_PROMPT_FILE` | (default minimal) | Path to a persona prompt file (e.g. `prompts/toris.md`) |
| `TTS_PROVIDER` | auto-detect | `elevenlabs` or `openai` |
| `STT_PROVIDER` | auto-detect | `elevenlabs` or `openai` |
| `STT_LANGUAGE` | auto | e.g. `en`, `pl` |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` (George) | See [ElevenLabs voice library](https://elevenlabs.io/app/voice-library) |
| `OPENAI_VOICE_ID` | `coral` | OpenAI voices: `alloy`, `ash`, `ballad`, `cedar`, `coral`, `echo`, `fable`, `juniper`, `marin`, `onyx`, `nova`, `sage`, `shimmer`, `verse` |
| `OPENAI_TTS_MODEL` | `gpt-4o-mini-tts` | Or `tts-1`, `tts-1-hd` |
| `OPENAI_STT_MODEL` | `whisper-1` | Or `gpt-4o-mini-transcribe`, `gpt-4o-transcribe` |
| `MAX_VOICE_RESPONSE_CHARS` | `500` | Truncate TTS input (controls cost) |
| `CLAUDE_TIMEOUT` | `300` | Max seconds to wait for a Claude response |
| `CLAUDE_WORKING_DIR` | `$HOME` | Directory Claude can read from |
| `CLAUDE_SANDBOX_DIR` | `$HOME/claude-sandbox` | Directory Claude can write to and execute in |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

## User Settings

Use `/settings` in Telegram to configure per-user preferences:

- **Mode** — Go All (auto-approve tools) or Approve (confirm each tool call)
- **Watch** — Off / Live (tool calls as they happen) / Debug (full SDK events)
- **Audio** — voice responses on/off
- **Speed** — voice playback speed (0.8x – 1.3x)
- **Automation cards** — compact or full display style

## Multi-Persona Setup

Run multiple AI personalities from the same codebase. Each gets its own:
- Telegram bot
- Voice and personality
- Sandbox directory
- Topic filter (for group chats)

### Docker multi-persona

Duplicate the service in `docker-compose.yml` with different env files:

```yaml
services:
  toris:
    env_file: docker/toris.env
    volumes:
      - toris-state:/home/claude/state
      - toris-sandbox:/home/claude/sandbox

  assistant2:
    env_file: docker/assistant2.env
    volumes:
      - assistant2-state:/home/claude/state
      - assistant2-sandbox:/home/claude/sandbox
```

### Persona prompt

See `prompts/toris.md` for the default persona. Key elements:

```markdown
# TORIS — Your Second Brain

You are TORIS, a voice-powered thinking partner built on Claude.

## Your Capabilities
- READ files from {read_dir}
- WRITE and EXECUTE in {sandbox_dir}
- Web search, research, note-taking

## CRITICAL — Voice Output Rules
- NO markdown formatting
- Speak in natural flowing sentences
```

---

## Docker Deployment Guide

### Building and running

```bash
# Build the image
docker-compose build

# Start
docker-compose up -d

# View logs
docker-compose logs -f toris

# Restart
docker-compose restart toris

# Stop
docker-compose down

# Stop and remove volumes (WARNING: deletes session history)
docker-compose down -v
```

### Configuration

```bash
cp docker/toris.env.example docker/toris.env
# Edit docker/toris.env — you only need TELEGRAM_BOT_TOKEN to start.
```

See the **Configuration** section above for the full env var reference.

### Credentials for subscription users

If you want to use an existing `claude /login` session from your host machine:

```yaml
# In docker-compose.yml, uncomment:
- ~/.claude/.credentials.json:/home/claude/.claude/.credentials.json:ro
```

### Data persistence

| Volume | Contents | Location |
|--------|----------|----------|
| `toris-state` | Session history & user settings | `/home/claude/state` |
| `toris-sandbox` | File operations sandbox | `/home/claude/sandbox` |
| `toris-claude-config` | Claude credentials & settings | `/home/claude/.claude` |

**Backup state:**
```bash
docker cp claude-voice-toris:/home/claude/state ./backup-state
```

### Health checks

```bash
docker-compose ps
docker-compose logs -f toris
```

---

## Systemd Deployment

For non-Docker production deployments on Linux.

### Setup

```bash
# Create deployment directory
sudo mkdir -p /opt/toris-agent
cd /opt/toris-agent

# Clone and install
sudo git clone --recurse-submodules https://github.com/toruai/toris-agent.git .
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

# Install Claude Code globally
sudo npm install -g @anthropic-ai/claude-code

# Create config
sudo mkdir -p /etc/toris-agent
sudo cp .env.example /etc/toris-agent/toris-agent.env
sudo $EDITOR /etc/toris-agent/toris-agent.env
```

### Service file

Create `/etc/systemd/system/toris-agent.service`:

```ini
[Unit]
Description=Toris Agent — Claude voice bot for Telegram
After=network.target

[Service]
Type=simple
User=claude
Group=claude
WorkingDirectory=/opt/toris-agent
EnvironmentFile=/etc/toris-agent/toris-agent.env
ExecStart=/opt/toris-agent/venv/bin/python bot.py
Restart=always
RestartSec=10

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/toris-agent/sandbox /var/lib/toris-agent/state

[Install]
WantedBy=multi-user.target
```

### Create user and directories

```bash
sudo useradd -r -s /bin/false claude
sudo mkdir -p /var/lib/toris-agent/{state,sandbox}
sudo chown -R claude:claude /var/lib/toris-agent
echo "CLAUDE_SANDBOX_DIR=/var/lib/toris-agent/sandbox" | sudo tee -a /etc/toris-agent/toris-agent.env
echo "STATE_DIR=/var/lib/toris-agent/state" | sudo tee -a /etc/toris-agent/toris-agent.env
```

### Manage the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable toris-agent
sudo systemctl start toris-agent

sudo systemctl status toris-agent
sudo journalctl -u toris-agent -f
```

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome / help |
| `/setup` | Conversational credential setup |
| `/health` | System & provider health check |
| `/new [name]` | Start a new session |
| `/continue` | Resume the last session |
| `/sessions` | List recent sessions |
| `/switch <id>` | Switch to a session by ID |
| `/search <term>` | Search sessions by keyword |
| `/cancel` | Cancel the current request |
| `/compact` | Summarize & compress the current session |
| `/status` | Current session info |
| `/settings` | Voice, mode, speed, watch mode |
| `/automations` | List & toggle scheduled automations |

## Security Considerations

- **Chat ID restriction** — only the configured chat ID can interact with the bot
- **Per-user allowlist** — `TELEGRAM_ALLOWED_USER_IDS` restricts which users are authorized inside that chat
- **Admin gating** — `TELEGRAM_ADMIN_USER_IDS` restricts `/setup` and credential commands
- **Anonymous denied** — when an allowlist is configured, anonymous / channel posts are rejected
- **Sandbox isolation** — Claude can only write / execute in the sandbox directory
- **Rate limiting** — 2s cooldown + 10/min per user
- **Token hygiene** — onboarding tokens are deleted from chat before saving; if delete fails, the bot refuses to save
- **No secrets in prompts** — keep API keys in env / `/setup`, never in persona prompt files

## Architecture

```
bot.py                 # Handler registration + startup
handlers/
  session.py           # /start /new /continue /sessions /switch /status /cancel /compact /search
  admin.py             # /setup /claude_token /elevenlabs_key /openai_key + settings callbacks
  messages.py          # voice / text / photo / onboarding / automations callbacks
auth.py                # Authorization, rate limiting, topic filtering
state_manager.py       # Thread-safe sessions + settings with atomic persistence
claude_service.py      # Claude Agent SDK wrapper + working indicator
voice_service.py       # TTS/STT with provider failover + health checks
automations.py         # RemoteTrigger integration
shared_state.py        # Cross-module pending approvals + cancel events
config.py              # Env var single source of truth
prompts/               # Persona prompt files
tests/                 # 170+ unit tests (uses asyncio.run, not pytest-asyncio)
```

## Development

```bash
source venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines and the test convention.

## How It Works

You send a voice message. Bot downloads it and pipes it through the STT provider (ElevenLabs Scribe or OpenAI Whisper). The transcript goes to the Claude Agent SDK, which reaches for real tools — Bash, Read, Grep, WebSearch, Edit, Write — to actually accomplish what you asked. If watch mode is on, you see each tool call stream into the chat as it happens. If approve mode is on, Claude waits for a ✓ before running anything with side effects. The final response comes back as text, and the TTS provider (ElevenLabs or OpenAI) speaks it back into your headphones.

The whole loop is usually a few seconds. No part of it is a wrapper around a single chat completion — the SDK runs a real agent loop, calling tools, reading results, deciding what to do next.

## License

[MIT](LICENSE) — built by [ToruAI](https://github.com/ToruAI). Fork it, change it, run your own persona.
