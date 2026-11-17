from pathlib import Path

import pytest

from app.core.config import get_domain_config
from app.services.store import Store

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


@pytest.fixture
def store() -> Store:
    s = Store(get_domain_config())
    for p in sorted(SAMPLES.iterdir()):
        s.ingest(p.read_bytes(), p.name)
    return s
