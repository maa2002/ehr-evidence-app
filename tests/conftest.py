"""Settings for every test: no test reads the workspace's freeze record
or final question sets, so none passes the freeze's gate and reads the
final case set (ADR 0029). Tests of the freeze name their own
directories."""

import os

import pytest

from ehr_evidence_app.assessment import freeze

# A directory that no test creates.
ABSENT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "no-freeze-here")


@pytest.fixture(autouse=True)
def no_workspace_freeze(monkeypatch):
    """Point the freeze record and the final question sets at a directory
    that does not exist."""
    assert not os.path.exists(ABSENT), ABSENT
    monkeypatch.setattr(freeze, "FREEZE_DIR", os.path.join(ABSENT, "freeze"))
    monkeypatch.setattr(freeze, "QUESTION_SETS",
                        os.path.join(ABSENT, "question-sets"))
