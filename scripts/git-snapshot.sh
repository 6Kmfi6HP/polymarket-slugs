#!/usr/bin/env bash
# Rolling data snapshot: update data/ without growing git history.
#
# Behavior:
#   - If HEAD is already a snapshot commit (author email matches
#     SNAPSHOT_AUTHOR_EMAIL), the data changes are folded into that commit
#     ("git commit --amend") and pushed with --force-with-lease, so the
#     branch history does not grow.
#   - Otherwise (HEAD is a manual/code commit), a new snapshot commit is
#     created on top; later runs amend it.
#   - With no data changes, the script exits successfully without committing.
#   - If the remote moved while this run was working (rare race), the push is
#     rejected and the script exits non-zero. No data is lost: the next
#     scheduled run re-fetches the lookback window and snapshots again.
#
# Environment:
#   SNAPSHOT_AUTHOR_NAME   author name for snapshot commits
#                          (default: polymarket-slug-bot)
#   SNAPSHOT_AUTHOR_EMAIL  author email used to recognize snapshot commits
#                          (default: bot@polymarket-slugs.local)
#   SNAPSHOT_PATHS         paths to snapshot, space-separated (default: data)
#   SNAPSHOT_BRANCH        target branch (default: main)
#
# NOTE: the lease for --force-with-lease is anchored to the remote-tracking
# ref from checkout. Do NOT "git fetch" here, or the lease would silently
# accept a remote that moved and clobber newer manual commits.

set -euo pipefail

AUTHOR_NAME="${SNAPSHOT_AUTHOR_NAME:-polymarket-slug-bot}"
AUTHOR_EMAIL="${SNAPSHOT_AUTHOR_EMAIL:-bot@polymarket-slugs.local}"
SNAPSHOT_PATHS="${SNAPSHOT_PATHS:-data}"
BRANCH="${SNAPSHOT_BRANCH:-main}"
SUBJECT_PREFIX="chore(data):"

# Identity for snapshot commits only (-c flags, no permanent config change).
GIT_ID=(-c "user.name=${AUTHOR_NAME}" -c "user.email=${AUTHOR_EMAIL}")

git add -A -- $SNAPSHOT_PATHS

if git diff --cached --quiet -- $SNAPSHOT_PATHS; then
  echo "No changes in [${SNAPSHOT_PATHS}] — nothing to snapshot."
  exit 0
fi

# Count added/removed CSV lines for the commit subject.
ROWS="$(git diff --cached --numstat -- $SNAPSHOT_PATHS \
  | awk '$1 != "-" {a += $1} $2 != "-" {r += $2} END {printf "%d/%d", a, r}')"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MSG="${SUBJECT_PREFIX} snapshot ${STAMP} (+${ROWS%%/*}/-${ROWS##*/} rows) [skip ci]"

HEAD_EMAIL="$(git log -1 --format='%ae')"
if [ "${HEAD_EMAIL}" = "${AUTHOR_EMAIL}" ]; then
  echo "HEAD is a snapshot commit — amending it (no new history entry)."
  git "${GIT_ID[@]}" commit --amend --reset-author --no-verify -m "${MSG}"
  PUSH_ARGS=(--force-with-lease)
else
  echo "HEAD is a regular commit — creating a new snapshot commit on top."
  git "${GIT_ID[@]}" commit --no-verify -m "${MSG}"
  PUSH_ARGS=()
fi

echo "Pushing snapshot to refs/heads/${BRANCH} ..."
if git push "${PUSH_ARGS[@]}" origin "HEAD:refs/heads/${BRANCH}"; then
  echo "Pushed: ${MSG}"
else
  echo "::warning::Push rejected — the remote branch moved while this run was working." >&2
  echo "::warning::No data lost: the next scheduled run re-fetches the lookback window and snapshots again." >&2
  exit 1
fi
