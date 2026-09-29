"""The coding speed test's tasks (they live in newal_code/benchmark.py, so `newal-code bench` has them too)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from newal_code.benchmark import TASKS, by_id, check, make  # noqa: E402,F401
