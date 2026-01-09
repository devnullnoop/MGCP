# Contributing to MGCP

Thank you for your interest in contributing to MGCP! This document provides guidelines for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/MGCP.git
   cd MGCP
   ```
3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   ```
4. Install in development mode:
   ```bash
   pip install -e ".[dev]"
   ```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_basic.py

# Run with coverage
pytest --cov=src/mgcp
```

### Code Style

We use [ruff](https://github.com/astral-sh/ruff) for linting (line-length: 120):

```bash
# Check for issues
ruff check src/ tests/

# Auto-fix where possible
ruff check --fix src/ tests/
```

**Important**: Treat warnings as errors. The CI will fail if there are any linter warnings.

### Making Changes

1. Create a branch for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes, ensuring:
   - All tests pass
   - Code follows existing style
   - New features have tests
   - Documentation is updated if needed

3. Commit with a clear message:
   ```bash
   git commit -m "Add feature: description of what you added"
   ```

4. Push and create a pull request

## Pull Request Guidelines

- Keep PRs focused on a single change
- Update documentation for new features
- Add tests for new functionality
- Ensure CI passes before requesting review

## Architecture Overview

Key files to understand:

| File | Purpose |
|------|---------|
| `src/mgcp/server.py` | MCP server and tool definitions |
| `src/mgcp/models.py` | Pydantic data models |
| `src/mgcp/persistence.py` | SQLite storage layer |
| `src/mgcp/vector_store.py` | ChromaDB integration |
| `src/mgcp/graph.py` | NetworkX graph operations |

## Adding New MCP Tools

1. Define the tool in `server.py` using the `@mcp.tool()` decorator
2. Add any new data models to `models.py`
3. Implement persistence in `persistence.py` if needed
4. Add tests in `tests/`
5. Update documentation in README.md and CLAUDE.md

## Adding Claude Code Hooks

MGCP uses hooks to make lessons proactive. Hooks are in `.claude/hooks/`:

| Event | Use Case |
|-------|----------|
| `SessionStart` | Load context at session start |
| `UserPromptSubmit` | Detect keywords in user messages |
| `PostToolUse` | React after tool execution |
| `PreCompact` | Save state before context compression |

Example hook pattern (UserPromptSubmit):
```python
import json, sys, re

hook_input = json.load(sys.stdin)
prompt = hook_input.get("prompt", "").lower()

if re.search(r"\bcommit\b", prompt):
    print("<reminder>Query lessons before committing</reminder>")
sys.exit(0)
```

Update `.claude/settings.json` to register new hooks.

## Reporting Issues

When reporting issues, please include:
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Error messages/stack traces

## Questions?

Open an issue with the "question" label or start a discussion.

## License

By contributing, you agree that your contributions will be licensed under the [O'Saasy License](LICENSE.md).
