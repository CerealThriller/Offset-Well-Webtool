import os
import sys
import tempfile
from pathlib import Path

# Settings read the data directory at import time, so set it first.
os.environ.setdefault("OWT_DATA_DIR", tempfile.mkdtemp(prefix="owt-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
