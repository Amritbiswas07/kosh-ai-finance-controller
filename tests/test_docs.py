"""The documents must agree with the code.

The same mistake happened three times: the code moved, the prose did not, and
the suite stayed green because nothing compared them. A stale figure in a README
is not a typo — it is a claim about the system that is no longer true, and it is
the first thing a reader checks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_every_documented_figure_is_still_true():
    if not (ROOT / "outputs" / "recon.json").exists():
        pytest.skip("no run to check against — `kosh recon` first")
    import check_docs
    problems = check_docs.check()
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)


def test_the_worked_example_points_at_a_record_that_exists():
    """The README quotes one finding in full. It must be a real one."""
    import json
    import re
    recon = ROOT / "outputs" / "recon.json"
    if not recon.exists():
        pytest.skip("no run to check against")
    findings = {f["key"]: f for f in json.loads(recon.read_text())["findings"]}
    readme = (ROOT / "README.md").read_text()
    quoted = re.findall(r"\*\*`(setl_\w+|pay_\w+|INV-[\w-]+|bank:\d+)`\*\*", readme)
    assert quoted, "the README no longer shows a worked example"
    for key in quoted:
        assert key in findings, f"README quotes {key}, which this run does not contain"


def test_every_command_in_the_readme_exists():
    """A command in the quick start that argparse does not know is a dead end."""
    import re
    from kosh.cli import main
    readme = (ROOT / "README.md").read_text()
    invoked = set(re.findall(r"kosh (\w[\w-]*)", readme))
    known = {"generate", "recon", "evaluate", "ask", "serve", "sync", "pull",
             "exception"}
    unknown = invoked - known
    assert not unknown, f"README invokes commands that do not exist: {unknown}"


def test_every_script_the_readme_names_is_present():
    import re
    readme = (ROOT / "README.md").read_text()
    for script in set(re.findall(r"scripts/(\w+)\.py", readme)):
        assert (ROOT / "scripts" / f"{script}.py").exists(), script
