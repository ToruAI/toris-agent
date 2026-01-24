# Claude Voice Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

**A voice-first interface to Claude's full agentic capabilities.** Not another chatbot wrapper.

<!-- Demo GIF here -->

Send a voice message, and Claude can search the web, read your files, write code, execute scripts, and respond with natural speech. All from Telegram.

## What Makes This Different

Most "voice AI" projects are thin wrappers around chat completions. This is different:

- **Agentic execution** - Claude can use tools: read files, search the web, write and execute code
- **Sandboxed safety** - All writes and execution happen in an isolated sandbox directory
- **Voice-native** - Built for voice input/output from the ground up, not text with TTS bolted on
- **Multi-persona** - Run different AI personalities from the same codebase, each with their own voice and sandbox
- **Session persistence** - Conversations continue across messages, even after restarts
- **Real-time control** - Watch mode streams tool calls, approve mode lets you authorize each action

## Features

| Feature | Description |
|---------|-------------|
| Voice transcription | ElevenLabs Scribe for accurate speech-to-text |
| Voice synthesis | ElevenLabs TTS with expressive voice settings |
| Claude Agent SDK | Official SDK with full tool access |
| Tool capabilities | Read, Grep, Glob, WebSearch, Bash, Edit, Write |
| Sandbox isolation | Claude can only write/execute in designated directory |
| Session management | Resume conversations, switch between sessions |
| Per-user settings | Audio on/off, voice speed, approval modes |
| Topic filtering | Multiple personas in one Telegram group |
| Rate limiting | Protect against abuse |

## Architecture

```
Telegram Voice Message
        |
        v
+-------------------+
|   Telegram Bot    |  <-- python-telegram-bot
+-------------------+
        |
        v
+-------------------+
| ElevenLabs Scribe |  <-- Speech-to-text
+-------------------+
        |
        v
+-------------------+
| Claude Agent SDK  |  <-- Full agentic capabilities
|   - WebSearch     |      (reads, writes, executes)
|   - Bash          |
|   - Read/Write    |
+-------------------+
        |
        v
+-------------------+
|  ElevenLabs TTS   |  <-- Text-to-speech
+-------------------+
        |
        v
   Voice Response
```

## Prerequisites

- **Telegram Bot** - Create one via [@BotFather](https://t.me/botfather)
- **ElevenLabs account** - API key from [elevenlabs.io](https://elevenlabs.io)

For Docker deployment:
- **Docker** and **Docker Compose**

For non-Docker deployment:
- **Python 3.12+**
- **Node.js 20+** (for Claude Code CLI)

## Deployment Options

This project supports two deployment modes:

### Option 1: Docker (Recommended for Production)

Best for production deployment and running multiple personas.

**Quick Start:**
```bash
# Clone the repository
git clone https://github.com/toruai/claude-voice-assistant.git
cd claude-voice-assistant

# Configure personas
cp docker/v.env.example docker/v.env
cp docker/tc.env.example docker/tc.env
# Edit docker/v.env and docker/tc.env with your API keys

# Start services
docker-compose up -d

# View logs
docker-compose logs -f v
docker-compose logs -f tc

# Stop services
docker-compose down
```

**Benefits:**
- Isolated sandboxes per persona
- Automatic restarts
- Easy multi-persona setup
- No Python/Node installation needed
- Persistent state across restarts

**Directory Structure:**
```
claude-voice-assistant/
├── Dockerfile
├── docker-compose.yml
├── docker/
│   ├── v.env          # V persona config
│   └── tc.env         # TC persona config
└── prompts/           # Shared persona prompts
```

See [Docker Deployment Guide](#docker-deployment-guide) for details.

### Option 2: Non-Docker (Systemd)

Best for development or single-persona deployments on Linux.

**Quick Start:**
```bash
# Clone and setup
git clone https://github.com/toruai/claude-voice-assistant.git
cd claude-voice-assistant
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Configure
cp .env.example .env
# Edit .env with your values

# Run
python bot.py
```

See [Systemd Deployment Guide](#systemd-deployment-guide) for production setup.

---

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_DEFAULT_CHAT_ID` | Your Telegram chat ID (security: only this chat can use the bot) |
| `ELEVENLABS_API_KEY` | API key from elevenlabs.io |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERSONA_NAME` | `Assistant` | Display name in logs |
| `SYSTEM_PROMPT_FILE` | - | Path to persona prompt file |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` | ElevenLabs voice (George) |
| `TELEGRAM_TOPIC_ID` | - | Filter to specific forum topic |
| `CLAUDE_WORKING_DIR` | `/home/youruser` | Directory Claude can read from |
| `CLAUDE_SANDBOX_DIR` | `/home/youruser/claude-voice-sandbox` | Directory Claude can write to |
| `MAX_VOICE_RESPONSE_CHARS` | `500` | Max characters for TTS |

## User Settings

Use `/settings` in Telegram to configure:

- **Mode**: "Go All" (auto-approve tools) or "Approve" (confirm each action)
- **Watch**: Stream tool calls to chat in real-time
- **Audio**: Enable/disable voice responses
- **Speed**: Voice playback speed (0.8x - 1.2x)

## Multi-Persona Setup

Run multiple AI personalities from the same codebase. Each gets its own:
- Telegram bot
- Voice and personality
- Sandbox directory
- Topic filter (for group chats)

### Directory Structure

```
/home/youruser/voice-agents/
├── v.env              # V persona config
├── tc.env             # TC persona config
└── sandboxes/
    ├── v/             # V's isolated sandbox
    └── tc/            # TC's isolated sandbox
```

### Example Persona Prompt

See `prompts/v.md` for a full example. Key elements:

```markdown
You are V, a brilliant and slightly cynical voice assistant.

## Your capabilities:
- You can READ files from anywhere in {read_dir}
- You can WRITE and EXECUTE only in {sandbox_dir}
- You have WebSearch for current information

## CRITICAL - Voice output rules:
- NO markdown formatting
- Speak in natural flowing sentences
```

---

## Docker Deployment Guide

### Building and Running

```bash
# Build the image
docker-compose build

# Start all personas
docker-compose up -d

# Start specific persona
docker-compose up -d v

# View logs (follow mode)
docker-compose logs -f v
docker-compose logs -f tc

# Restart a persona
docker-compose restart v

# Stop all
docker-compose down

# Stop and remove volumes (WARNING: deletes session history)
docker-compose down -v
```

### Configuration

Each persona has its own environment file in `docker/`:

```bash
# Required files (create from examples):
docker/v.env    # V persona configuration
docker/tc.env   # TC persona configuration
```

**Key environment variables:**
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather
- `TELEGRAM_DEFAULT_CHAT_ID` - Your Telegram chat ID (security)
- `TELEGRAM_TOPIC_ID` - Topic filter (for multi-persona groups)
- `ELEVENLABS_API_KEY` - ElevenLabs API key
- `ELEVENLABS_VOICE_ID` - Voice selection
- `PERSONA_NAME` - Display name in logs
- `SYSTEM_PROMPT_FILE` - Path to persona prompt

### Data Persistence

Docker volumes store persistent data:

| Volume | Contents | Location |
|--------|----------|----------|
| `v-state` | V session history & settings | `/home/claude/state` |
| `v-sandbox` | V file operations sandbox | `/home/claude/sandbox` |
| `tc-state` | TC session history & settings | `/home/claude/state` |
| `tc-sandbox` | TC file operations sandbox | `/home/claude/sandbox` |

**Backup state:**
```bash
# Export session data
docker cp claude-voice-v:/home/claude/state ./backup-v-state

# Import session data
docker cp ./backup-v-state/. claude-voice-v:/home/claude/state
docker-compose restart v
```

### Adding More Personas

Edit `docker-compose.yml`:

```yaml
  new-persona:
    build: .
    container_name: claude-voice-new
    env_file:
      - docker/new.env
    volumes:
      - new-state:/home/claude/state
      - new-sandbox:/home/claude/sandbox
      - ./prompts:/home/claude/app/prompts:ro
    restart: unless-stopped
    networks:
      - voice-assistants

volumes:
  new-state:
    driver: local
  new-sandbox:
    driver: local
```

### Health Checks

Docker monitors bot health automatically. Check status:

```bash
# Container health
docker-compose ps

# If unhealthy, check logs
docker-compose logs v
```

---

## Systemd Deployment Guide

For non-Docker production deployments on Linux.

### Setup

```bash
# Create deployment directory
mkdir -p /opt/claude-voice-assistant
cd /opt/claude-voice-assistant

# Clone and install
git clone https://github.com/toruai/claude-voice-assistant.git .
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Install Claude Code globally
npm install -g @anthropic-ai/claude-code

# Create config directory
mkdir -p /etc/claude-voice
cp .env.example /etc/claude-voice/v.env
# Edit /etc/claude-voice/v.env with your values
```

### Service File

Create `/etc/systemd/system/claude-voice-v.service`:

```ini
[Unit]
Description=Claude Voice Assistant - V
After=network.target

[Service]
Type=simple
User=claude
Group=claude
WorkingDirectory=/opt/claude-voice-assistant
EnvironmentFile=/etc/claude-voice/v.env
ExecStart=/opt/claude-voice-assistant/.venv/bin/python bot.py
Restart=always
RestartSec=10

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/claude-voice/v-sandbox /var/lib/claude-voice/v-state

[Install]
WantedBy=multi-user.target
```

### Create User and Directories

```bash
# Create service user
useradd -r -s /bin/false claude

# Create state and sandbox directories
mkdir -p /var/lib/claude-voice/{v-state,v-sandbox}
chown -R claude:claude /var/lib/claude-voice

# Set sandbox path in env file
echo "CLAUDE_SANDBOX_DIR=/var/lib/claude-voice/v-sandbox" >> /etc/claude-voice/v.env
```

### Manage Service

```bash
# Enable and start
systemctl daemon-reload
systemctl enable claude-voice-v
systemctl start claude-voice-v

# Check status
systemctl status claude-voice-v

# View logs
journalctl -u claude-voice-v -f

# Restart
systemctl restart claude-voice-v
```

### Multiple Personas with Systemd

Create separate service files and env files:
- `/etc/systemd/system/claude-voice-v.service` + `/etc/claude-voice/v.env`
- `/etc/systemd/system/claude-voice-tc.service` + `/etc/claude-voice/tc.env`

Each persona needs its own sandbox and state directories.

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/new [name]` | Start new session |
| `/continue` | Resume last session |
| `/sessions` | List all sessions |
| `/switch <id>` | Switch to specific session |
| `/status` | Current session info |
| `/settings` | Configure audio, speed, mode |
| `/health` | System health check |

## Security Considerations

- **Chat ID restriction** - Only configured chat ID can interact with the bot
- **Sandbox isolation** - Claude can only write/execute in the sandbox directory
- **Rate limiting** - Built-in protection against abuse (2s cooldown, 10/minute limit)
- **No secrets in prompts** - Keep API keys in `.env`, not in persona files

## Development

```bash
# Install dev dependencies
pip install pytest pytest-asyncio pytest-cov

# Run tests
pytest test_bot.py -v

# Run with coverage
pytest test_bot.py --cov=bot --cov-report=term-missing
```

## How It Works

1. **Voice input**: Telegram receives voice message, bot downloads it
2. **Transcription**: ElevenLabs Scribe converts speech to text
3. **Processing**: Claude Agent SDK processes with full tool access
4. **Tool execution**: Claude can search web, read files, execute code in sandbox
5. **Response**: Text response sent to chat
6. **Voice output**: ElevenLabs TTS converts response to speech (if enabled)

The Claude Agent SDK provides real agentic capabilities - Claude can autonomously use multiple tools to accomplish tasks, not just respond to prompts.

## License

[MIT](LICENSE) - ToruAI 2026
