from __future__ import annotations

import pytest

from row_bot.brain import ReferenceBrain
from tests.contracts.brain.suite import BrainContract

pytestmark = pytest.mark.contract


class TestReferenceBrain(BrainContract):
    @pytest.fixture
    def brain_factory(self):
        return lambda spaces, clock: ReferenceBrain(spaces, clock=clock)
