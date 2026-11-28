"""Tests for scripts/git-snapshot.sh (rolling snapshot commit logic).

Each test builds a throwaway git repo (work + bare origin) and runs the real
shell script, so the amend / force-with-lease behavior is exercised end to end
without network access.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-snapshot.sh"

BOT_NAME = "snapshot-bot"
BOT_EMAIL = "snapshot-bot@test.local"
HUMAN_NAME = "human"
HUMAN_EMAIL = "human@test.local"


def git(work: Path, *args: str, env: dict | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class GitSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)

        # Empty global/system git config so tests are hermetic.
        self.git_config_global = base / "gitconfig"
        self.git_config_global.touch()
        self.base_env = dict(
            os.environ,
            GIT_CONFIG_GLOBAL=str(self.git_config_global),
            GIT_CONFIG_SYSTEM=os.devnull,
            SNAPSHOT_AUTHOR_NAME=BOT_NAME,
            SNAPSHOT_AUTHOR_EMAIL=BOT_EMAIL,
        )

        self.origin = base / "origin.git"
        self.work = base / "work"
        git(base, "init", "--bare", "-q", "-b", "main", str(self.origin))
        git(base, "init", "-q", "-b", "main", str(self.work))
        self._identity(self.work, HUMAN_NAME, HUMAN_EMAIL)
        (self.work / "code.py").write_text("print('v1')\n")
        (self.work / "data").mkdir()
        (self.work / "data" / "5m.csv").write_text("slug,asset\na-5m-1,BTC\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "feat: initial code")
        git(self.work, "remote", "add", "origin", str(self.origin))
        git(self.work, "push", "-q", "-u", "origin", "main")

    # -- helpers ----------------------------------------------------------

    def _identity(self, work: Path, name: str, email: str) -> None:
        git(work, "config", "user.name", name)
        git(work, "config", "user.email", email)

    def run_snapshot(self, work: Path | None = None) -> subprocess.CompletedProcess:
        work = work or self.work
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=work,
            env=self.base_env,
            capture_output=True,
            text=True,
        )

    def change_data(self, slug: str) -> None:
        path = self.work / "data" / "5m.csv"
        with path.open("a") as f:
            f.write(f"{slug},ETH\n")

    def commit_count(self, work: Path | None = None) -> int:
        return len(git(work or self.work, "rev-list", "HEAD").splitlines())

    def head_email(self, work: Path | None = None) -> str:
        return git(work or self.work, "log", "-1", "--format=%ae")

    def head_subject(self, work: Path | None = None) -> str:
        return git(work or self.work, "log", "-1", "--format=%s")

    def origin_sha(self) -> str:
        """Current tip of the real remote (not a possibly stale tracking ref)."""
        return git(self.work, "ls-remote", "origin", "refs/heads/main").split()[0]

    # -- tests ------------------------------------------------------------

    def test_first_snapshot_commits_once(self) -> None:
        self.change_data("b-5m-2")
        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commit_count(), 2)  # code commit + 1 snapshot
        self.assertTrue(self.head_subject().startswith("chore(data): snapshot "))
        self.assertTrue(self.head_subject().endswith(" [skip ci]"))
        self.assertEqual(self.head_email(), BOT_EMAIL)
        self.assertEqual(self.origin_sha(), git(self.work, "rev-parse", "HEAD"))
        self.assertIn("b-5m-2", (self.work / "data" / "5m.csv").read_text())

    def test_second_run_amends_without_growing_history(self) -> None:
        self.change_data("b-5m-2")
        self.assertEqual(self.run_snapshot().returncode, 0)

        self.change_data("c-5m-3")
        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commit_count(), 2)  # amended, not appended
        self.assertEqual(self.head_email(), BOT_EMAIL)
        self.assertEqual(self.origin_sha(), git(self.work, "rev-parse", "HEAD"))
        content = (self.work / "data" / "5m.csv").read_text()
        self.assertIn("b-5m-2", content)
        self.assertIn("c-5m-3", content)

    def test_no_changes_exits_zero_without_commit(self) -> None:
        self.change_data("b-5m-2")
        self.assertEqual(self.run_snapshot().returncode, 0)
        head_before = git(self.work, "rev-parse", "HEAD")

        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to snapshot", result.stdout)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head_before)
        self.assertEqual(self.commit_count(), 2)

    def test_manual_commit_on_top_gets_new_snapshot(self) -> None:
        self.change_data("b-5m-2")
        self.assertEqual(self.run_snapshot().returncode, 0)

        (self.work / "code.py").write_text("print('v2')\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "feat: v2")
        self.change_data("c-5m-3")
        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commit_count(), 4)  # code x2 + 1 old snapshot + 1 new
        self.assertTrue(self.head_subject().startswith("chore(data): snapshot "))
        self.assertEqual(self.head_email(), BOT_EMAIL)

        # The next run must amend this new snapshot commit, not stack on it.
        self.change_data("d-5m-4")
        self.assertEqual(self.run_snapshot().returncode, 0)
        self.assertEqual(self.commit_count(), 4)
        self.assertIn("d-5m-4", (self.work / "data" / "5m.csv").read_text())

    def test_remote_race_is_rejected_and_recovers(self) -> None:
        self.change_data("b-5m-2")
        self.assertEqual(self.run_snapshot().returncode, 0)

        # Another clone moves the remote forward while our run is in flight.
        clone = self.work.parent / "clone2"
        git(self.work.parent, "clone", "-q", str(self.origin), str(clone))
        self._identity(clone, HUMAN_NAME, HUMAN_EMAIL)
        (clone / "code.py").write_text("print('from elsewhere')\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "feat: manual push")
        git(clone, "push", "-q", "origin", "main")
        remote_after_manual = git(clone, "rev-parse", "HEAD")

        # Our run still sits on the old tip: the push must be rejected.
        self.change_data("c-5m-3")
        result = self.run_snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("push", result.stderr.lower())
        self.assertEqual(self.origin_sha(), remote_after_manual)  # not clobbered

        # Recovery, as the next Actions run sees it: a fresh checkout of the
        # new remote tip (the failed run's local amended commit is discarded;
        # its data is re-fetched from the API lookback window).
        git(self.work, "fetch", "origin")
        git(self.work, "reset", "--hard", "origin/main")
        self.change_data("c-5m-3")
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        # v1 code + snapshot #1 (pushed before the race) + manual push + new snapshot
        self.assertEqual(self.commit_count(), 4)
        self.assertEqual(self.origin_sha(), git(self.work, "rev-parse", "HEAD"))
        self.assertIn("c-5m-3", (self.work / "data" / "5m.csv").read_text())

    def test_subject_reports_row_counts(self) -> None:
        self.change_data("b-5m-2")
        self.change_data("c-5m-3")
        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(self.head_subject(), r"\(\+2/-0 rows\)")


if __name__ == "__main__":
    unittest.main()
