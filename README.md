# Techainer Shared GitHub Configuration

Centralized reusable workflows for AI-powered code review across all Techainer repositories.

## Reusable Workflows

| Workflow | Description | Trigger |
|----------|-------------|---------|
| `reusable-review.yml` | Automated PR code review using Claude's `/code-review` plugin (4 parallel agents + confidence scoring) | `pull_request: opened, synchronize, reopened` |
| `reusable-claude-mention.yml` | Interactive `@claude` mention handler in PRs & issues | `@claude` in comments |
| `reusable-docs-sync.yml` | Auto-update documentation (CLAUDE.md, README.md) after PR merge | `pull_request: closed (merged)` |

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

Write project-specific rules. The review plugin reads this file automatically.

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

The `/code-review` plugin launches **4 parallel agents**:
1. **Agent 1+2**: CLAUDE.md compliance check (redundant for reliability)
2. **Agent 3**: Bug detection focused on diff only
3. **Agent 4**: Git blame/history context analysis

Each issue is scored 0-100 for confidence. Only issues ≥80 are posted as inline comments with suggested fixes.
