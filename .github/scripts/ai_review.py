#!/usr/bin/env python3
"""AI code review gate — được reusable-ai-review.yml gọi. Xem đầu file workflow để biết luồng.

Tách khỏi `run: |` của YAML vì 2 lẽ: (1) GHA nội suy MỌI biểu thức đô-la-ngoặc-nhọn trong
run-block kể cả trong heredoc — sự cố 15/07 làm vỡ parse cả 16 repo; (2) test được ở local.

Không phụ thuộc `gh` CLI (self-hosted trần không có): gọi GitHub REST bằng urllib + GH_TOKEN.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

E = os.environ.get
EVENT = E("EVENT", "")
REPO = E("REPO", "")
PROFILE = E("PROFILE", "code")
PR = E("PR", "")
PR_HEAD = E("PR_HEAD_SHA", "")
BEFORE = E("BEFORE", "")
AFTER = E("AFTER", "") or "HEAD"
MODEL = E("MODEL", "") or "ai-review"

DIFF_LIMIT = 45000          # ký tự diff gửi LLM (~12k token) — trước là 60KB diff + 50KB full file
MAX_TOKENS = 4096
BLOCK_CONFIDENCE = 0.8      # blocker dưới ngưỡng này chỉ là cảnh báo, không chặn

MARKER_RE = re.compile(r"<!-- ai-review sha=(?P<sha>[0-9a-f]{7,40}) verdict=(?P<verdict>[A-Z]+) -->")


class LLMDown(Exception):
    """LLM không trả được kết quả dùng được → fail-open CÓ cảnh báo."""


# ---------------------------------------------------------------- tiện ích
def run_git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def git(*args):
    code, out = run_git(*args)
    return out if code == 0 else ""


def gh(path, method="GET", data=None):
    url = (E("GITHUB_API_URL") or "https://api.github.com") + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + E("GH_TOKEN", ""),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except Exception as e:  # comment/dedupe là phụ — lỗi API không được làm chết gate
        print("GitHub API lỗi (bỏ qua):", method, path, str(e)[:150])
        return None


# ---------------------------------------------------------------- chọn file
_EXCL_SUFFIX = (".lock", "-lock.json", "lock.yaml", "lock.yml", "go.sum", ".min.js", ".min.css", ".map",
                ".svg", ".png", ".jpg", ".jpeg", ".ico", ".gif", ".webp", ".woff", ".woff2", ".ttf",
                ".pdf", ".lockb", ".md", ".mdx", ".snap")
_EXCL_PART = ("/dist/", "/build/", "/vendor/", "/node_modules/", "/__generated__/", ".generated.", "/gen/",
              "/__snapshots__/")


def excluded(path):
    """CI/workflow, docs, lock, generated, binary, bản dịch: không đáng tốn token, và review nhầm chính
    gate CI (ci-cd.yml) từng làm FAIL oan."""
    q = "/" + path.lower()
    if q.startswith(("/.github/", "/docs/")):
        return True
    if q.endswith(_EXCL_SUFFIX) or any(s in q for s in _EXCL_PART):
        return True
    return q.endswith(".json") and ("/locales/" in q or "/i18n/" in q)


_TAG_LINE = re.compile(r"^[+-]\s*(?:-\s*)?(?P<k>newTag|tag|image)\s*:\s*['\"]?(?P<v>\S+?)['\"]?\s*$")


def tag_only(diff_u0):
    """Profile deploy: commit CHỈ đổi tag image (CI ghi newTag) → PASS không cần LLM.
    `image:` mới phải giữ nguyên repo/registry của dòng bị thay — đổi sang image lạ vẫn đi LLM."""
    changed = [ln for ln in diff_u0.splitlines()
               if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    if not changed:
        return False
    old_repos, new_repos = set(), set()
    for ln in changed:
        m = _TAG_LINE.match(ln)
        if not m:
            return False
        if m["k"] == "image":
            repo = m["v"].rsplit("@", 1)[0]
            repo = repo.rsplit(":", 1)[0] if ":" in repo.rsplit("/", 1)[-1] else repo
            (old_repos if ln[0] == "-" else new_repos).add(repo)
    return new_repos <= old_repos


# ---------------------------------------------------------------- verdict
_COMPILE_CLAIM = re.compile(
    r"(kh[oô]ng\s+(?:compile|build|bi[êe]n\s*d[ịi]ch)|compile[\s-]*error|syntax[\s-]*error|"
    r"sai\s*c[uú]\s*ph[áa]p|l[oô]i\s*c[uú]\s*ph[áa]p|build[\s-]*fail|"
    r"(?:won'?t|cannot|does\s*not|will\s*not)\s*compile|kh[oô]ng\s*bi[êe]n\s*d[ịi]ch\s*[đd][ưuơợ]?[ơợ]c)", re.I)


def normalize_issues(raw):
    out = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, str):  # model trả kiểu cũ (chuỗi) → coi là góp ý
            it = {"severity": "minor", "problem": it}
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity", "minor")).lower()
        try:
            conf = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        out.append({
            "severity": sev if sev in ("blocker", "major", "minor") else "minor",
            "file": str(it.get("file") or ""),
            "line": it.get("line"),
            "problem": str(it.get("problem") or "").strip(),
            "fix": str(it.get("fix") or "").strip(),
            "confidence": conf,
        })
    return [i for i in out if i["problem"]]


def decide(issues):
    """Verdict tính bằng code, KHÔNG tin field verdict của model.
    - Bỏ claim compile/syntax: bước build mới là nguồn sự thật (model hay nhầm cú pháp, vd Go elision).
    - FAIL ⇔ còn blocker có confidence ≥ ngưỡng."""
    dropped = [i for i in issues if _COMPILE_CLAIM.search(i["problem"] + " " + i["fix"])]
    for d in dropped:
        print("⏭️  DROP claim compile/syntax (build đã lo):", d["problem"][:180])
    kept = [i for i in issues if i not in dropped]
    for i in kept:
        if i["severity"] == "blocker" and i["confidence"] < BLOCK_CONFIDENCE:
            i["severity"] = "major"  # nghi ngờ → cảnh báo, không chặn
    verdict = "FAIL" if any(i["severity"] == "blocker" for i in kept) else "PASS"
    return verdict, kept


# ---------------------------------------------------------------- prompt
CODE_RULES = (
    "BUG/LOGIC: null/None, off-by-one, race, sai điều kiện, thiếu await, sai kiểu dữ liệu runtime. "
    "SECURITY: injection (SQL/XSS/command), secret/credential hardcode, thiếu validate input, path traversal, lộ dữ liệu. "
    "ERROR-HANDLING: nuốt lỗi, thiếu cleanup/rollback, resource leak. "
    "ARCHITECTURE: breaking-change API không migrate, trùng lặp đáng tách."
)
DEPLOY_RULES = (
    "AN TOÀN DEPLOY (manifest k8s / config): không hạ replicas về 0 ngoài ý muốn, không thêm secret cleartext mới, "
    "không giảm resource limit/request vô lý (OOM/thiếu), đổi image NÊN chỉ là đổi TAG (đổi image-name/registry lạ phải nghi ngờ), "
    "sửa ConfigMap/env phải khớp app (không xoá key app đang dùng), migration/job DB phải an toàn (không mất dữ liệu)."
)
EXAMPLE = ('{"summary":"Thêm API xoá lớp; thiếu kiểm quyền.","issues":[{"severity":"blocker","file":"api/class.go",'
           '"line":42,"problem":"DeleteClass không kiểm role admin, user thường xoá được lớp của trường khác.",'
           '"fix":"Gọi requireRole(ctx, \\"admin\\") trước khi xoá.","confidence":0.9}]}')


def system_prompt(profile):
    """Phần TĨNH theo profile — để đầu prompt cho vLLM prefix-cache dùng lại giữa các lần review."""
    role = "senior DevOps review manifest triển khai" if profile == "deploy" else "senior code reviewer"
    rules = DEPLOY_RULES if profile == "deploy" else CODE_RULES
    return (
        "Bạn là " + role + ". Review DIFF (git diff kèm nguyên hàm bao quanh) theo RULES. "
        "Truy vết tác động: data flow, null, edge case, side-effect, tương tác giữa các hàm — không chỉ dòng đổi bề mặt.\n"
        "RULES: " + rules + "\n"
        "MỨC ĐỘ:\n"
        "- blocker: CHẮC CHẮN gây hỏng/nguy hiểm khi chạy (bug rõ, lỗ hổng thật, mất dữ liệu, breaking-change không migrate).\n"
        "- major: rủi ro đáng sửa nhưng chưa chắc gây hỏng.\n"
        "- minor: góp ý nhỏ.\n"
        "KHÔNG báo: style/format/naming, thiếu test, nghi ngờ mơ hồ. "
        "KHÔNG báo lỗi compile/cú pháp: pipeline đã build trước bước này, nghĩ là sai cú pháp thì gần như chắc bạn nhầm. "
        "File CI/config: chỉ báo lỗ hổng thật; reusable `Techainer/.github/...@main` là nội bộ, tin cậy. "
        "confidence (0-1) = độ chắc chắn lỗi là THẬT. Mỗi issue phải nêu file, dòng, vấn đề, cách sửa cụ thể. "
        "Không có lỗi → issues rỗng.\n"
        "Trả về DUY NHẤT 1 JSON, tiếng Việt có dấu, đúng dạng: "
        '{"summary":"1-2 câu: thay đổi làm gì + đánh giá","issues":[{"severity":"blocker|major|minor","file":"...",'
        '"line":0,"problem":"...","fix":"...","confidence":0.0}]}\n'
        "Ví dụ: " + EXAMPLE
    )


def call_llm(system, user):
    base = (E("OPENAI_BASE_URL") or "").rstrip("/")
    key = E("OPENAI_API_KEY") or ""
    if not base or not key:
        raise LLMDown("thiếu secret OPENAI_BASE_URL/OPENAI_API_KEY")
    payload = {"model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        payload.update(json.loads(E("LLM_EXTRA_BODY") or "{}"))
    except ValueError:
        print("LLM_EXTRA_BODY không phải JSON — bỏ qua")

    def post(p):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(p).encode(), headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=170) as r:
            return json.load(r)

    try:
        try:
            resp = post(payload)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            # backend không nhận tham số phụ (response_format / extra) → thử lại bản tối giản
            print("LLM 400 với tham số phụ → thử lại không kèm:", e.read()[:200])
            resp = post({k: payload[k] for k in ("model", "temperature", "max_tokens", "messages")})
    except urllib.error.HTTPError as e:
        raise LLMDown("HTTP %s: %s" % (e.code, e.read()[:200].decode("utf-8", "ignore")))
    except Exception as e:
        raise LLMDown(type(e).__name__ + ": " + str(e)[:200])

    choice = (resp.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    usage = resp.get("usage") or {}
    print("LLM usage: prompt=%s completion=%s finish=%s" % (
        usage.get("prompt_tokens"), usage.get("completion_tokens"), choice.get("finish_reason")))
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------- comment
def fmt_issue(n, i):
    loc = i["file"] + (":" + str(i["line"]) if i["line"] else "")
    s = str(n) + ". " + (("`" + loc + "` — ") if loc else "") + i["problem"]
    return s + ((" → **Sửa:** " + i["fix"]) if i["fix"] else "")


def render(head, summary, issues, marker_sha, verdict, ref, role, collapse_minor):
    parts = ["## 🤖 AI Review — " + head, "", "> " + summary, ""]
    groups = [("⛔ Chặn deploy", "blocker"), ("⚠️ Nên sửa", "major"), ("💡 Góp ý", "minor")]
    for title, sev in groups:
        items = [i for i in issues if i["severity"] == sev]
        if not items:
            continue
        body = "\n".join(fmt_issue(n, i) for n, i in enumerate(items, 1))
        if sev == "minor" and collapse_minor:
            parts += ["<details><summary>" + title + " (" + str(len(items)) + ")</summary>", "", body, "</details>", ""]
        else:
            parts += ["### " + title + " (" + str(len(items)) + ")", body, ""]
    if not issues:
        parts += ["_Không có vấn đề._", ""]
    parts.append("<sub>" + role + " · model `" + MODEL + "` · " + ref + " · profile `" + PROFILE + "`</sub>")
    parts.append("<!-- ai-review sha=" + marker_sha + " verdict=" + verdict + " -->")
    return "\n".join(parts)


def publish(comment):
    if EVENT == "pull_request":
        # 1 comment/PR, sửa tại chỗ thay vì đẻ comment mới mỗi lần push vào PR
        mine = [c for c in (gh("/repos/%s/issues/%s/comments?per_page=100" % (REPO, PR)) or [])
                if MARKER_RE.search(c.get("body") or "")]
        if mine:
            gh("/repos/%s/issues/comments/%s" % (REPO, mine[-1]["id"]), "PATCH", {"body": comment})
        else:
            gh("/repos/%s/issues/%s/comments" % (REPO, PR), "POST", {"body": comment})
    else:
        # notify-push (deploy repo) đọc comment trên commit để đưa vào card CHỜ DUYỆT
        gh("/repos/%s/commits/%s/comments" % (REPO, AFTER), "POST", {"body": comment})


def summary_and_log(comment, head, ref):
    gs = E("GITHUB_STEP_SUMMARY")
    if gs:
        try:
            with open(gs, "a", encoding="utf-8") as f:
                f.write(comment + "\n")
        except OSError:
            pass
    bar = "─" * 66
    print("\n" + bar + "\n🤖 AI REVIEW — " + head + "   [" + ref.replace("`", "") + " · profile " + PROFILE + "]\n" + bar)
    print(MARKER_RE.sub("", comment))
    print(bar)


# ---------------------------------------------------------------- dedupe
def prior_review():
    """Push: commit này đã được review chưa? (a) đã có comment marker trên chính SHA (cùng SHA đẩy
    staging rồi production, hoặc standalone + build-gate cùng chạy); (b) SHA thuộc PR đã merge mà
    head cuối của PR đã review. Trả (verdict, nguồn, body) hoặc None."""
    for c in reversed(gh("/repos/%s/commits/%s/comments?per_page=100" % (REPO, AFTER)) or []):
        m = MARKER_RE.search(c.get("body") or "")
        if m and AFTER.startswith(m["sha"]) and m["verdict"] in ("PASS", "FAIL"):
            return m["verdict"], "commit `" + AFTER[:7] + "` đã review trước đó", c["body"]
    for pr in gh("/repos/%s/commits/%s/pulls" % (REPO, AFTER)) or []:
        if not pr.get("merged_at"):
            continue
        head_sha = (pr.get("head") or {}).get("sha", "")
        for c in reversed(gh("/repos/%s/issues/%s/comments?per_page=100" % (REPO, pr["number"])) or []):
            m = MARKER_RE.search(c.get("body") or "")
            if m and head_sha.startswith(m["sha"]) and m["verdict"] in ("PASS", "FAIL"):
                return m["verdict"], "PR #%s (head `%s`)" % (pr["number"], head_sha[:7]), c["body"]
    return None


# ---------------------------------------------------------------- validate deploy
def validate_config(names):
    errors, checked = [], []
    if not all(any(os.access(os.path.join(p, b), os.X_OK) for p in os.environ.get("PATH", "").split(os.pathsep))
               for b in ("kustomize", "kubeconform")):
        print("::warning::thiếu kustomize/kubeconform — bỏ qua validate cứng")
        return errors
    for d in sorted({os.path.dirname(f) for f in names if f.endswith((".yaml", ".yml"))}):
        if not d or not any(os.path.exists(os.path.join(d, k)) for k in ("kustomization.yaml", "kustomization.yml")):
            continue
        checked.append(d)
        kb = subprocess.run(["kustomize", "build", d], capture_output=True, text=True)
        if kb.returncode != 0:
            errors.append(d + " — kustomize build LỖI: " + kb.stderr.strip()[:300])
            continue
        kc = subprocess.run(["kubeconform", "-strict", "-ignore-missing-schemas", "-summary", "-"],
                            input=kb.stdout, capture_output=True, text=True)
        if kc.returncode != 0:
            errors.append(d + " — kubeconform LỖI (schema k8s không hợp lệ): " + (kc.stdout + kc.stderr).strip()[:300])
    print("Validate config dirs:", checked, "| errors:", len(errors))
    return errors


# ---------------------------------------------------------------- main
def main():
    role = "senior DevOps review manifest triển khai" if PROFILE == "deploy" else "senior code reviewer"
    is_pr = EVENT == "pull_request"
    marker_sha = PR_HEAD if is_pr and PR_HEAD else AFTER
    ref = ("PR #" + PR) if is_pr else ("commit `" + AFTER[:7] + "`")

    def finish(verdict, head, summary, issues):
        comment = render(head, summary, issues, marker_sha, verdict, ref, role, collapse_minor=is_pr)
        summary_and_log(comment, head, ref)
        publish(comment)
        print("Verdict:", verdict, "| issues:", len(issues))
        sys.exit(1 if verdict == "FAIL" else 0)

    if not is_pr:
        prior = prior_review()
        if prior:
            verdict, source, body = prior
            body = MARKER_RE.sub("", body).strip()
            head = "✅ PASS" if verdict == "PASS" else "⚠️ FAIL — cần sửa"
            comment = ("♻️ Dùng lại kết quả đã review ở " + source + " — không gọi lại AI.\n\n" + body +
                       "\n<!-- ai-review sha=" + AFTER + " verdict=" + verdict + " -->")
            summary_and_log(comment, head, ref)
            if source.startswith("PR"):  # (a) đã có comment trên chính commit thì không đăng trùng
                publish(comment)
            print("Verdict:", verdict, "(dùng lại) |", source)
            sys.exit(1 if verdict == "FAIL" else 0)

    # ---- phạm vi diff ----
    if is_pr:
        rng = ["HEAD^1", "HEAD"]  # checkout@v4 ở pull_request = merge commit, parent 1 = base
    elif BEFORE and set(BEFORE) != {"0"} and run_git("cat-file", "-e", BEFORE + "^{commit}")[0] == 0:
        rng = [BEFORE, AFTER]
    else:
        rng = ["HEAD~1", "HEAD"]
    names = [n for n in git("diff", "--name-only", *rng).splitlines() if n]
    kept = [n for n in names if not excluded(n)]
    if not kept:
        finish("PASS", "✅ PASS", "Chỉ đổi file CI/docs/lock/generated — không cần review.", [])

    val_errors = validate_config(kept) if PROFILE == "deploy" else []
    if val_errors:
        finish("FAIL", "⚠️ FAIL — cần sửa",
               "Config KHÔNG build được / sai schema k8s — CHẶN deploy (kustomize build + kubeconform).",
               [{"severity": "blocker", "file": "", "line": None, "problem": "🔧 " + e, "fix": "", "confidence": 1.0}
                for e in val_errors])

    if PROFILE == "deploy" and tag_only(git("diff", "-U0", *rng, "--", *kept)):
        finish("PASS", "✅ PASS", "Chỉ đổi tag image (cùng repo image) — an toàn, không cần AI review.", [])

    diff = git("diff", "-W", *rng, "--", *kept)  # -W: kèm nguyên hàm bao quanh thay cho gửi full file
    if len(diff) > DIFF_LIMIT:
        diff = git("diff", "-U5", *rng, "--", *kept)
    truncated = len(diff) > DIFF_LIMIT
    diff = diff[:DIFF_LIMIT]
    skipped = len(names) - len(kept)
    user = ("File đổi (" + str(len(kept)) + "): " + ", ".join(kept[:60]) +
            (("\n(bỏ qua " + str(skipped) + " file CI/docs/lock/generated)") if skipped else "") +
            ("\n(DIFF bị cắt ở " + str(DIFF_LIMIT) + " ký tự — chỉ đánh giá phần thấy được)" if truncated else "") +
            "\n\nDIFF:\n" + diff)

    try:
        out = call_llm(system_prompt(PROFILE), user)
        if not isinstance(out, dict) or "issues" not in out:
            raise LLMDown("model trả sai định dạng JSON")
    except LLMDown as e:
        reason = str(e)
        print("::warning title=AI review KHÔNG chạy::" + reason + " — commit này CHƯA được review (fail-open).")
        finish("ERROR", "⚠️ KHÔNG CHẠY ĐƯỢC",
               "AI review lỗi (" + reason + ") — commit này **CHƯA được review**, không chặn build. "
               "Tự rà diff trước khi Duyệt.", [])

    verdict, issues = decide(normalize_issues(out.get("issues")))
    head = "✅ PASS" if verdict == "PASS" else "⚠️ FAIL — cần sửa"
    finish(verdict, head, str(out.get("summary") or "").strip() or "(không có tóm tắt)", issues)


if __name__ == "__main__":
    main()
