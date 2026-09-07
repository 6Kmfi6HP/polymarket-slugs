"""Polymarket UPDOWN data pipeline.

Three independent layers:

  pull    Gamma API -> data/raw/*.jsonl.xz   lossless (minus presentation
          boilerplate), append-only observations, never rewritten by hand
  build   raw observations -> data/{date}/{interval}.csv + summaries
          pure projection, rebuildable at any time (``--force``)
  snapshot  scripts/git-snapshot.sh (unchanged rolling git snapshot)
"""

from pathlib import Path


def default_data_dir() -> Path:
    """Return the repo-root data directory."""
    return Path(__file__).resolve().parent.parent.parent / "data"
