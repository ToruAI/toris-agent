# Contributing to Toris Agent

Thanks for your interest in contributing! This document outlines how to get set up and the conventions we follow.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone --recurse-submodules https://github.com/toruai/toris-agent.git
   cd toris-agent
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate       # Linux / macOS
   # or: venv\Scripts\activate    # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install the Claude Code CLI** (needed for `/health` and some tests):
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

5. **Copy and configure environment:**
   ```bash
   cp .env.example .env
   # Only TELEGRAM_BOT_TOKEN is required to start.
   # Claude auth and voice credentials can be set via /setup in Telegram.
   ```

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_auth.py -v

# Specific test
pytest tests/test_auth.py::TestAllowedUserIds -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

## Test Convention

Tests use `asyncio.run()` directly — **not** `@pytest.mark.asyncio`. This keeps
test setup minimal and avoids pytest-asyncio event loop quirks across Python versions.

```python
def test_something():
    async def _run():
        result = await my_async_func()
        assert result == expected
    asyncio.run(_run())
```

## Code Style

- **Python 3.11+** — use modern features (match, union syntax, etc.) where they improve clarity
- Follow PEP 8
- Add type hints for function signatures
- Keep functions focused; if they're over ~50 lines, consider splitting
- Don't add docstrings, comments, or type annotations to code you didn't change
- Don't add error handling, fallbacks, or validation for scenarios that can't happen

## Import Rule

Handlers import from `state_manager`, `auth`, `shared_state`, `claude_service`,
`voice_service`, and `config`. **Never import from `bot.py`** — this prevents
circular imports. `bot.py` is the entry point; it imports handlers, not the
other way around.

## Project Structure

```
toris-agent/
├── bot.py                 # Entry point + handler registration
├── handlers/              # Command + message handlers
│   ├── session.py         # /start /new /continue /sessions /switch /search /status /cancel /compact
│   ├── admin.py           # /setup /claude_token /elevenlabs_key /openai_key + settings & approval callbacks
│   └── messages.py        # voice / text / photo / onboarding / automations callbacks
├── auth.py                # Authorization + rate limiting + topic filtering
├── state_manager.py       # Thread-safe state singleton with atomic persistence
├── claude_service.py      # Claude Agent SDK wrapper + working indicator
├── voice_service.py       # TTS/STT with provider failover + health checks
├── automations.py         # RemoteTrigger scheduled tasks
├── shared_state.py        # Cross-module pending approvals + cancel events
├── config.py              # Env var single source of truth
├── prompts/               # Persona prompt files (e.g. toris.md)
├── tests/                 # Test suite (asyncio.run, not pytest-asyncio)
├── docker/                # Docker env templates
├── .env.example           # Environment template
└── requirements.txt       # Python dependencies
```

## Making Changes

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make your changes** with clear, focused commits.

3. **Run tests** to make sure nothing breaks:
   ```bash
   pytest tests/ -v
   ```

4. **Submit a pull request** with:
   - Clear description of what changed
   - Why the change is needed
   - Any breaking changes or migration steps noted

## Pull Request Guidelines

- Keep PRs focused on a single change
- Update documentation if adding features
- Add tests for new functionality — aim for the same style as existing tests
- Ensure all tests pass before submitting
- Don't bundle unrelated refactors with feature PRs

## Reporting Issues

When reporting bugs, please include:

1. Python version (`python --version`)
2. Operating system
3. Steps to reproduce
4. Expected vs actual behaviour
5. Relevant log output (`journalctl -u toris-agent -f` for systemd, `docker-compose logs toris` for Docker)
6. Bot `/health` output if the issue involves providers or Claude

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
