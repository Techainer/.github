"""Test các hàm thuần của ai_review.py: python3 -m unittest discover -s .github/scripts"""
import unittest

import ai_review as ar


class Excluded(unittest.TestCase):
    def test_skip(self):
        for p in (".github/workflows/ci-cd.yml", "docs/a.go", "README.md", "yarn.lock", "go.sum",
                  "web/dist/app.js", "src/locales/vi.json", "public/i18n/en.json", "a/__snapshots__/x.snap"):
            self.assertTrue(ar.excluded(p), p)

    def test_keep(self):
        for p in ("src/app.ts", "cmd/main.go", "k8s/main/app/kustomization.yaml", "src/i18n/index.ts",
                  "package.json"):
            self.assertFalse(ar.excluded(p), p)


class TagOnly(unittest.TestCase):
    def test_newtag(self):
        d = "--- a/k\n+++ b/k\n@@ -5 +5 @@\n-    newTag: production-abc1234\n+    newTag: production-def5678\n"
        self.assertTrue(ar.tag_only(d))

    def test_image_same_repo(self):
        d = "-        image: techainer1t/app:prod-1\n+        image: techainer1t/app:prod-2\n"
        self.assertTrue(ar.tag_only(d))

    def test_image_other_repo(self):
        d = "-        image: techainer1t/app:prod-1\n+        image: evil/app:prod-2\n"
        self.assertFalse(ar.tag_only(d))

    def test_registry_with_port(self):
        d = "-  image: reg.local:5000/app:1\n+  image: reg.local:5000/app:2\n"
        self.assertTrue(ar.tag_only(d))

    def test_other_change(self):
        d = "-    newTag: a\n+    newTag: b\n-  replicas: 2\n+  replicas: 0\n"
        self.assertFalse(ar.tag_only(d))

    def test_empty(self):
        self.assertFalse(ar.tag_only(""))


class Decide(unittest.TestCase):
    def issues(self, *items):
        return ar.normalize_issues(list(items))

    def test_blocker_fails(self):
        v, _ = ar.decide(self.issues({"severity": "blocker", "problem": "null deref", "confidence": 0.9}))
        self.assertEqual(v, "FAIL")

    def test_low_confidence_blocker_downgraded(self):
        v, kept = ar.decide(self.issues({"severity": "blocker", "problem": "có thể race", "confidence": 0.5}))
        self.assertEqual((v, kept[0]["severity"]), ("PASS", "major"))

    def test_compile_claim_dropped(self):
        v, kept = ar.decide(self.issues({"severity": "blocker", "problem": "Code không compile được", "confidence": 1}))
        self.assertEqual((v, kept), ("PASS", []))

    def test_legacy_string_issue(self):
        v, kept = ar.decide(self.issues("file.go — thiếu kiểm lỗi"))
        self.assertEqual((v, kept[0]["severity"]), ("PASS", "minor"))

    def test_bad_values(self):
        kept = ar.normalize_issues([{"severity": "CRITICAL", "problem": "x", "confidence": "?"}, 3, {"problem": ""}])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["severity"], "minor")


class Marker(unittest.TestCase):
    def test_roundtrip(self):
        iss = ar.normalize_issues([{"severity": "minor", "problem": "p", "file": "a.go", "line": 3}])
        body = ar.render("✅ PASS", "ok", iss, "abc1234def", "PASS", "PR #1", "r", collapse_minor=True)
        m = ar.MARKER_RE.search(body)
        self.assertEqual((m["sha"], m["verdict"]), ("abc1234def", "PASS"))
        self.assertIn("AI Review", body)  # notify-push lọc comment theo chuỗi này
        self.assertIn("<details>", body)


if __name__ == "__main__":
    unittest.main()
