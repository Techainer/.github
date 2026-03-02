# Techainer Shared GitHub Configuration

Repo chứa reusable workflows cho Claude Code Review, dùng chung cho tất cả repos trong org.

## Reusable Workflows

| Workflow | Mô tả |
|----------|-------|
| `reusable-review.yml` | AI code review tự động khi mở PR (dùng `/code-review` plugin) |
| `reusable-claude-mention.yml` | Xử lý `@claude` mention trong PR/issues |

## Setup cho repo mới

### 1. Thêm secret `ZAI_AUTH_TOKEN`

Repo Settings → Secrets → Actions → New repository secret:
- Name: `ZAI_AUTH_TOKEN`
- Value: z.ai API key

### 2. Thêm caller workflows

Tạo 2 files trong `.github/workflows/`:

**`claude-review.yml`**:
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

**`claude.yml`**:
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

### 3. Tạo `CLAUDE.md` trong repo

Viết project-specific rules. Plugin tự đọc file này khi review.

## Skip Review

Thêm label `skip-review` vào PR để skip auto-review.
