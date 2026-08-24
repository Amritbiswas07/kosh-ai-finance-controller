import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.generate import build                      # noqa: E402
from kosh.ingest import build_batches                # noqa: E402
from kosh.match import reconcile                     # noqa: E402


@pytest.fixture(scope="session")
def corpus():
    """One generated world, shared across the suite. No model is loaded."""
    ds, gt, inj = build(seed=4242)
    return ds, gt, inj


@pytest.fixture(scope="session")
def run(corpus):
    ds, gt, _ = corpus
    batches = build_batches(ds)
    return ds, gt, batches, reconcile(ds, batches)
