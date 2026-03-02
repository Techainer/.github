# Techainer Shared GitHub Configuration

Centralized reusable workflows for AI-powered code review and documentation sync across all Techainer repositories.

## Reusable Workflows

| Workflow | Description | Trigger |
|----------|-------------|---------|
| `reusable-review.yml` | Automated PR code review — single-pass analysis covering bugs, security, error handling, architecture, and performance | `pull_request: opened, synchronize, reopened` |
| `reusable-claude-mention.yml` | Interactive `@claude` mention handler in PRs & issues | `@claude` in comments |
| `reusable-docs-sync.yml` | Auto-update documentation (CLAUDE.md, README.md, docstrings) after PR merge | `pull_request: closed (merged)` |

## Setup for a New Repository

### 1. Add Repository Secret

Go to **Repo Settings → Secrets → Actions → New repository secret**:
- **Name**: `ZAI_AUTH_TOKEN`
- **Value**: Your z.ai API key

### 2. Add Caller Workflows

Create the following files in `.github/workflows/`:

**`claude-review.yml`** — Automated code review on every PR:
```yaml
name: Claude Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  review:
    uses: techainer/.github/.github/workflows/reusable-review.yml@main
    secrets:
      ZAI_AUTH_TOKEN: ${{ secrets.ZAI_AUTH_TOKEN }}
```

**`claude.yml`** — `@claude` interactive handler:
```yaml
name: Claude
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  pull_request_review:
    types: [submitted]
  issues:
    types: [opened, assigned]
jobs:
  claude:
    uses: techainer/.github/.github/workflows/reusable-claude-mention.yml@main
    secrets:
      ZAI_AUTH_TOKEN: ${{ secrets.ZAI_AUTH_TOKEN }}
```

**`claude-docs-update.yml`** — Auto docs sync on merge (optional):
```yaml
name: Claude Docs Sync
on:
  pull_request:
    types: [closed]
    paths:
      - "**/*.py"   # adjust file filter per project
jobs:
  docs:
    uses: techainer/.github/.github/workflows/reusable-docs-sync.yml@main
    secrets:
      ZAI_AUTH_TOKEN: ${{ secrets.ZAI_AUTH_TOKEN }}
```

### 3. Create `CLAUDE.md` in Your Repository

Write project-specific rules. The review reads this file automatically.

Example:
```markdown
# Project: My App
## Architecture: NestJS with DDD
## Rules:
- Use dependency injection via constructors
- All endpoints must have DTO validation
- Write unit tests for every service
```

## Skipping Review

Add the label `skip-review` to any PR to bypass automatic code review.

## How It Works

### Code Review (`reusable-review.yml`)
Single-pass review in ~1 minute. Analyzes the PR diff across **5 dimensions**:

| Dimension | Checks |
|-----------|--------|
| **Bugs** | Syntax errors, type mismatches, null handling, missing imports |
| **Security** | Injection, hardcoded secrets, input validation |
| **Error Handling** | Bare except, swallowed errors, missing cleanup |
| **Architecture** | CLAUDE.md violations, code duplication, breaking changes |
| **Performance** | N+1 queries, unbounded loops, blocking I/O |

Output: structured comment with severity levels (🔴 Critical / 🟡 Important / 🔵 Minor) and a verdict (✅ Approve / ⚠️ Suggestions / ❌ Request changes).

### Docs Sync (`reusable-docs-sync.yml`)
Runs after PR merge. Checks if merged changes require documentation updates:
- **CLAUDE.md** — architecture, conventions, patterns
- **README.md** — API endpoints, setup, configuration
- **Docstrings** — new/changed public functions

Only updates docs that are actually outdated. Commits changes and reports on the PR.

### Mention Handler (`reusable-claude-mention.yml`)
Responds to `@claude` in PR comments and issues. Can answer questions, suggest fixes, or implement changes.

## Cost Control

All workflows use z.ai API with configurable `max_turns`:
- Review: 6 turns (default)
- Docs sync: 8 turns (default)
- Mention: 15 turns (default)

Override in caller workflow:
```yaml
with:
  max_turns: "10"
```
