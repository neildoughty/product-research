"""
Track seen article/repo URLs to avoid re-processing week to week.
seen.json is committed back to the repo by the GitHub Actions workflow.
"""

import json
import logging
import os

SEEN_PATH = "seen.json"
logger = logging.getLogger(__name__)


def load_seen(path: str = SEEN_PATH) -> set:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        try:
            return set(json.load(f))
        except (json.JSONDecodeError, TypeError):
            logger.warning("seen.json is malformed — starting fresh")
            return set()


def save_seen(seen: set, path: str = SEEN_PATH):
    with open(path, "w") as f:
        json.dump(sorted(seen), f, indent=2)
