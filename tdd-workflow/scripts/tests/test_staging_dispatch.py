"""2026-11-26: /deploy dispatches the deploy workflow to staging and pushes no tag
(the staging release writes the `deploy-*` tag itself, as its "staging verified"
marker). Its approval must cover that one staging dispatch and nothing that reaches
production, and with no skill declaring `deploy-tag` a tag push is refused whatever
is approved."""
import contextlib
import unittest
from unittest import mock

import test_release_approval_gate as base
from test_release_approval_gate import FakeWorld, Transcript, bash_decision, gate, on_branch, status_text

STAGING = "gh workflow run deploy.yml --ref main -f target=staging"
WITH_OFFS = STAGING + " -f accept_off=chat_shadow_runs,corpus_wal_archive"
NOT_STAGING_ONLY = (
    "gh workflow run promote.yml -f sha=abc",
    "gh workflow run deploy.yml --ref main",
    "gh workflow run deploy.yml --ref main -f target=both",
    "gh workflow run deploy.yml --ref hotfix -f target=production",
    STAGING + " -f target=both",
    "gh workflow run deploy.yml -f target=both -f target=staging",
    STAGING + " -ftarget=production",
    STAGING + " --raw-field=target=production",
    STAGING + " -F target=both",
    STAGING + " --json",
    "gh workflow run deploy.yml --ref target=staging",
    "gh workflow run deploy.yml -f note=target=staging",
)


def shipped():
    """The release skills as the plugin ships them now: deploy is a staging dispatch,
    and no skill has a follow-on."""
    both = contextlib.ExitStack()
    both.enter_context(mock.patch.dict(base.ACTIONS, {"deploy": ("dispatch-staging",)}))
    both.enter_context(mock.patch.dict(base.FOLLOW_ON, clear=True))
    return both


class StagingDispatchClassTest(unittest.TestCase):
    def c(self, cmd):
        return gate.classify(cmd, "/repo", branch_of=on_branch("feature"))

    def test_only_a_dispatch_that_can_reach_staging_alone_is_a_staging_dispatch(self):
        for cmd in (STAGING, WITH_OFFS, "gh workflow run deploy.yml --raw-field target=staging",
                    "gh workflow run deploy.yml -F target=staging --ref main"):
            self.assertEqual(self.c(cmd), {"dispatch-staging"}, cmd)
        for cmd in NOT_STAGING_ONLY:
            self.assertEqual(self.c(cmd), {"dispatch"}, cmd)


class DeployApprovalTest(unittest.TestCase):
    def test_a_deploy_approval_covers_one_staging_dispatch_and_nothing_else(self):
        with shipped():
            t = Transcript().typed("approve", "deploy")
            self.assertIsNone(bash_decision(t, STAGING))
            self.assertIsNone(bash_decision(t, WITH_OFFS))
            for cmd in NOT_STAGING_ONLY:
                reason = bash_decision(t, cmd)
                self.assertIn("release action (dispatch)", reason or "allowed", cmd)
                self.assertIn("/approve promote", reason, cmd)
            self.assertIn("release action (dispatch)", bash_decision(t, "gh run rerun 42") or "allowed")
            self.assertIn("deploy (dispatch-staging)", status_text(t))
            t.bash(WITH_OFFS, output="https://github.com/EndoxiaBV/Endoxia/actions/runs/77")
            reason = bash_decision(t, STAGING)
            self.assertIn("dispatch-staging", reason or "allowed", "one approval, one staging dispatch")
            self.assertIn("/approve deploy", reason)
            self.assertIsNone(bash_decision(t, "gh run rerun 77 --failed"),
                              "the run its own dispatch printed is re-runnable once")

    def test_a_dispatch_approval_still_covers_a_staging_dispatch(self):
        with shipped():
            t = Transcript().typed("approve", "promote")
            self.assertIsNone(bash_decision(t, STAGING))
            t.bash(STAGING)
            self.assertIn("dispatch", bash_decision(t, "gh workflow run promote.yml -f sha=abc") or "allowed",
                          "the staging dispatch used the promote approval")

    def test_the_staging_approval_is_spent_before_a_dispatch_approval(self):
        with shipped():
            t = Transcript().typed("approve", "promote deploy")
            t.bash(STAGING)
            self.assertIsNone(bash_decision(t, "gh workflow run promote.yml -f sha=abc"))
            self.assertIsNone(bash_decision(t, STAGING), "the promote approval is still open and covers it")

    def test_the_standing_approval_of_deploy_reaches_staging_only(self):
        with shipped():
            world = FakeWorld(standing={"deploy"})
            self.assertIsNone(bash_decision(Transcript(), STAGING, world=world))
            for cmd in NOT_STAGING_ONLY:
                self.assertIn("dispatch", bash_decision(Transcript(), cmd, world=FakeWorld(standing={"deploy"}))
                              or "allowed", cmd)
            self.assertIsNone(bash_decision(Transcript(), STAGING, world=FakeWorld(standing={"promote"})))


class NoSkillPushesATagTest(unittest.TestCase):
    def test_a_tag_push_is_refused_whatever_is_approved(self):
        with shipped():
            every = "ship quickship merge deploy promote release hotfix"
            for t, world in ((Transcript(), FakeWorld()),
                             (Transcript().typed("approve", every), FakeWorld()),
                             (Transcript(), FakeWorld(standing=set(every.split())))):
                for cmd in ("git push origin deploy-20261126-101500", "git push origin refs/tags/deploy-1"):
                    reason = bash_decision(t, cmd, world=world)
                    self.assertIn("no approval covers it", reason or "allowed", cmd)
                    self.assertIn("staging verified", reason)
                    self.assertNotIn("/approve <skill>", reason)

    def test_a_repository_skill_that_declares_tag_pushes_keeps_them(self):
        # The fixture table of test_release_approval_gate.py: deploy declares deploy-tag.
        t = Transcript().typed("approve", "deploy")
        self.assertIsNone(bash_decision(t, "git push origin deploy-1"))
        self.assertIn("/approve deploy", bash_decision(Transcript(), "git push origin deploy-1"))


if __name__ == "__main__":
    unittest.main()
