"""The owner approves a change by watching its evidence on the PR before it merges
(owner, 2026-10-22). VERIFY opens the PR with the evidence; /ship lands exactly the
PR head that evidence was recorded for. verify-state.py binds the two:

    verify-state.py evidence            # the manifest is complete (before record)
    verify-state.py review-marker       # the marker the PR body carries
    verify-state.py review-covers <PR>  # the PR head is the reviewed work, and its
                                        # evidence is that verdict's
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "verify-state.py"
MANIFEST = ".claude/verify-evidence/manifest.json"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.root, "config", key, value)
        (self.root / ".gitignore").write_text(".claude/\n")
        (self.root / "app.py").write_text("print('v1')\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "chore: base")
        git(self.root, "checkout", "-q", "-b", "feature")
        (self.root / "app.py").write_text("print('v2')\n")
        # A stand-in `gh` on PATH answers `pr view` from a file and logs its arguments.
        self.bin = base / "bin"
        self.bin.mkdir()
        self.pr_file, self.log = base / "pr.json", base / "gh-argv.jsonl"
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\nimport json, sys\n"
                      f"open({str(self.log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      f"text = open({str(self.pr_file)!r}).read()\n"
                      "if text == 'FAIL':\n    print('HTTP 502', file=sys.stderr); sys.exit(1)\n"
                      "print(text)\n")
        gh.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, env=env)

    def ok(self, *args):
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def refused(self, *args, says=()):
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for needle in says:
            self.assertIn(needle, result.stdout)
        return result.stdout

    def commit(self, message="feat: work"):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")


class EvidenceManifestTest(Repo):
    """Owner, 2026-10-22: a user-observable change carries a recording of every user
    flow, or says per flow why not; a change with nothing to observe says so."""

    def setUp(self):
        super().setUp()
        self.ok("record")
        self.verdict = self.ok("verdict-id").strip()

    def write(self, manifest, files=()):
        manifest.setdefault("verdictId", self.verdict)
        directory = self.root / ".claude" / "verify-evidence" / "r1"
        directory.mkdir(parents=True, exist_ok=True)
        for name in files:
            (directory / name).write_bytes(b"\x00media")
        (self.root / MANIFEST).write_text(json.dumps(manifest))

    def observable(self, **override):
        manifest = {
            "verdict": "READY", "verdictId": self.verdict, "recordedAt": "2026-10-22T10:00:00Z",
            "slice": "docs/slices/1-x.md", "observable": True, "outOfScope": "",
            "flows": ["Upload a file and open it"],
            "items": [
                {"path": ".claude/verify-evidence/r1/01-list.png", "kind": "screenshot",
                 "criterion": "The list shows the file", "caption": "The list with the new file."},
                {"path": ".claude/verify-evidence/r1/02-upload.mp4", "kind": "recording",
                 "criterion": "Upload a file and open it", "flow": "Upload a file and open it",
                 "caption": "The file is uploaded and opens."},
            ],
            "notRun": [],
        }
        manifest.update(override)
        return manifest

    def test_a_complete_observable_manifest_passes(self):
        self.write(self.observable(), files=("01-list.png", "02-upload.mp4"))
        out = self.ok("evidence")
        self.assertIn("1 user flow", out)

    def test_a_flow_without_a_recording_or_a_reason_is_refused(self):
        # Mutation: dropping the per-flow check passes a manifest with screenshots only.
        manifest = self.observable()
        manifest["items"] = manifest["items"][:1]
        self.write(manifest, files=("01-list.png",))
        self.refused("evidence", says=("Upload a file and open it", "no recording"))

    def test_a_flow_named_not_run_with_its_reason_passes(self):
        manifest = self.observable(notRun=[{"criterion": "Upload a file and open it",
                                            "reason": "the parser service is not in this profile"}])
        manifest["items"] = manifest["items"][:1]
        self.write(manifest, files=("01-list.png",))
        self.assertIn("NOT RUN", self.ok("evidence"))

    def test_an_observable_change_names_its_flows(self):
        self.write(self.observable(flows=[], items=[]))
        self.refused("evidence", says=("names no user flow",))

    def test_a_missing_file_is_refused(self):
        self.write(self.observable(), files=("01-list.png",))
        self.refused("evidence", says=("02-upload.mp4", "does not exist"))

    def test_a_path_outside_the_evidence_directory_is_refused(self):
        manifest = self.observable()
        manifest["items"][0]["path"] = "app.py"
        self.write(manifest, files=("02-upload.mp4",))
        self.refused("evidence", says=("app.py", "outside .claude/verify-evidence/"))

    def test_evidence_that_git_would_commit_is_refused(self):
        (self.root / ".gitignore").write_text("")
        self.write(self.observable(), files=("01-list.png", "02-upload.mp4"))
        self.refused("evidence", says=("not ignored by git",))

    def test_a_not_observable_change_states_why_and_carries_no_items(self):
        self.write({"verdict": "READY", "recordedAt": "2026-10-22T10:00:00Z", "slice": "x",
                    "observable": False, "outOfScope": "tooling only: a test helper", "flows": [],
                    "items": [], "notRun": []})
        self.assertIn("out of scope: tooling only", self.ok("evidence"))
        self.write({"verdict": "READY", "recordedAt": "2026-10-22T10:00:00Z", "slice": "x",
                    "observable": False, "outOfScope": "", "flows": [], "items": [], "notRun": []})
        self.refused("evidence", says=("outOfScope",))

    def test_the_verdict_id_is_the_recorded_digest(self):
        state = json.loads((self.root / ".claude" / ".verify-state.json").read_text())
        self.assertEqual(self.verdict, state["treeDigest"])

    def test_a_manifest_without_its_verdict_is_refused(self):
        manifest = self.observable()
        del manifest["verdictId"]
        (self.root / ".claude" / "verify-evidence").mkdir(parents=True, exist_ok=True)
        (self.root / MANIFEST).write_text(json.dumps(manifest))
        self.refused("evidence", says=("verdictId",))

    def test_evidence_of_an_earlier_verdict_is_refused_after_a_new_review(self):
        # Review finding (2026-10-22): a conflict resolved while landing gets a new
        # verdict; the recordings still show the code before it. Mutation: without the
        # verdict check the old manifest passes.
        self.write(self.observable(), files=("01-list.png", "02-upload.mp4"))
        self.ok("evidence")
        (self.root / "app.py").write_text("print('resolved a conflict')\n")
        self.ok("record")
        self.refused("evidence", says=("belongs to an earlier verdict", "NOT RUN"))

    def test_no_verdict_id_without_a_verdict_covering_the_tree(self):
        (self.root / "app.py").write_text("print('unreviewed')\n")
        self.refused("verdict-id", says=("not covered",))

    def test_a_missing_manifest_is_refused(self):
        self.refused("evidence", says=("manifest.json is missing",))

    def test_the_documented_example_is_a_valid_shape(self):
        # The manifest VERIFY documents must pass the check it is held to.
        text = (Path(__file__).resolve().parents[2] / "skills" / "feature" / "phases" / "04-verify.md").read_text()
        block = text[text.index("### The evidence manifest"):].split("```json\n", 1)[1].split("```", 1)[0]
        manifest = json.loads(block)
        manifest["verdictId"] = self.verdict
        for item in manifest["items"]:
            self.assertRegex(item["path"], r"^\.claude/verify-evidence/r\d+/")
            (self.root / item["path"]).parent.mkdir(parents=True, exist_ok=True)
            (self.root / item["path"]).write_bytes(b"\x00media")
        self.write(manifest)
        self.ok("evidence")


class ReviewCoversTest(Repo):
    """The PR the owner watched is exactly the reviewed work, with that VERIFY's evidence."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_script("record").returncode, 0)
        self.write_evidence()
        self.head = self.commit()

    def write_evidence(self):
        """A complete manifest for the verdict now recorded (nothing to observe)."""
        (self.root / ".claude" / "verify-evidence").mkdir(parents=True, exist_ok=True)
        (self.root / MANIFEST).write_text(json.dumps({
            "verdict": "READY", "verdictId": self.ok("verdict-id").strip(), "recordedAt": "2026-10-22T10:00:00Z",
            "slice": "x", "observable": False, "outOfScope": "tooling only", "flows": [], "items": [],
            "notRun": []}))

    def marker(self):
        out = self.ok("review-marker").strip()
        self.assertRegex(out, r"^<!-- verify-verdict: [0-9a-f]{64} -->$")
        return out

    def answer(self, head, body, state="OPEN"):
        self.pr_file.write_text(json.dumps({"state": state, "headRefOid": head, "body": body,
                                            "number": 42, "url": "https://github.com/o/r/pull/42"}))

    def test_the_marker_names_the_verdict_that_covers_head(self):
        marker = self.marker()
        state = json.loads((self.root / ".claude" / ".verify-state.json").read_text())
        self.assertIn(state["treeDigest"], marker)

    def test_no_marker_without_a_covering_verdict(self):
        (self.root / "app.py").write_text("print('unreviewed')\n")
        self.commit("feat: unreviewed")
        self.refused("review-marker", says=("not covered",))

    def test_the_reviewed_head_with_its_evidence_is_covered(self):
        self.answer(self.head, f"## Evidence\n{self.marker()}\n")
        out = self.ok("review-covers", "42")
        self.assertIn(self.head[:12], out)
        argv = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(argv[-1][:3], ["pr", "view", "42"])

    def test_a_new_commit_after_the_review_is_named(self):
        # Mutation: comparing only the marker passes an unreviewed head.
        marker = self.marker()
        (self.root / "app.py").write_text("print('after review')\n")
        head = self.commit("fix: after the review")
        self.answer(head, f"## Evidence\n{marker}\n")
        self.refused("review-covers", "42", says=("changed after the review", "app.py"))

    def test_a_marker_needs_this_verdicts_evidence(self):
        (self.root / "app.py").write_text("print('fixed')\n")
        self.ok("record")
        self.commit("fix: the review finding")
        self.refused("review-marker", says=("belongs to an earlier verdict",))
        self.write_evidence()
        self.marker()

    def test_landing_refuses_when_the_evidence_on_disk_is_an_earlier_verdicts(self):
        # The PR's marker can name the new verdict while the files are still the old
        # ones (a re-record after a conflict). Mutation: review-covers ignoring the
        # manifest passes this.
        (self.root / "app.py").write_text("print('resolved')\n")
        self.ok("record")
        head = self.commit("fix: conflict resolution")
        state = json.loads((self.root / ".claude" / ".verify-state.json").read_text())
        self.answer(head, f"<!-- verify-verdict: {state['treeDigest']} -->")
        self.refused("review-covers", "42", says=("belongs to an earlier verdict",))

    def test_a_second_verify_makes_the_evidence_stale(self):
        # A fix was reviewed again: the PR still shows the first VERIFY's evidence.
        marker = self.marker()
        (self.root / "app.py").write_text("print('fixed')\n")
        self.assertEqual(self.run_script("record").returncode, 0)
        self.write_evidence()
        head = self.commit("fix: the review finding")
        self.answer(head, f"## Evidence\n{marker}\n")
        self.refused("review-covers", "42", says=("refresh the evidence",))

    def test_a_clean_integration_of_main_keeps_the_evidence(self):
        marker = self.marker()
        git(self.root, "checkout", "-q", "main")
        (self.root / "main_only.py").write_text("m = 1\n")
        self.commit("feat: main moves")
        git(self.root, "checkout", "-q", "feature")
        git(self.root, "update-ref", "refs/remotes/origin/main", "main")
        git(self.root, "merge", "-q", "--no-edit", "-m", "chore: integrate origin/main before shipping", "origin/main")
        self.ok("integrate")
        self.answer(git(self.root, "rev-parse", "HEAD"), f"## Evidence\n{marker}\n")
        self.ok("review-covers", "42")

    def test_a_body_without_the_marker_is_refused(self):
        self.answer(self.head, "## Evidence\nnothing\n")
        self.refused("review-covers", "42", says=("carries no verify-verdict marker",))

    def test_a_pr_that_is_not_open_is_refused(self):
        self.answer(self.head, self.marker(), state="MERGED")
        self.refused("review-covers", "42", says=("MERGED",))

    def test_a_head_that_is_not_fetched_is_named(self):
        self.answer("0" * 40, self.marker())
        self.refused("review-covers", "42", says=("not in this clone", "git fetch"))

    def test_an_unreadable_pr_is_refused(self):
        self.pr_file.write_text("FAIL")
        self.refused("review-covers", "42", says=("HTTP 502",))


if __name__ == "__main__":
    unittest.main()
