"""The desktop pet's OS adapter on this machine: it loads, reads the cursor and
lists other apps' windows as bare frames (read-only; no window is created)."""

from __future__ import annotations

import math
import sys

import pytest

from row_bot.buddy.pet import OtherWindow
from row_bot.buddy.pet_native import pet_native

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="the desktop pet is Windows and macOS only")
def test_the_adapter_reads_the_cursor_and_other_windows_frames_here():
    native = pet_native()
    assert native is not None

    point = native.cursor()
    assert point is None or (len(point) == 2 and all(math.isfinite(value) for value in point))

    windows = native.windows()
    assert len(windows) <= 32
    for window in windows:
        assert isinstance(window, OtherWindow)
        assert window.id > 0  # a real window number/handle, read with the right type
        assert window.frame.width >= 120 and window.frame.height >= 80

    front = native.foreground()
    assert front is None or (isinstance(front, OtherWindow) and front.frame.width >= 120)


def test_elsewhere_there_is_no_adapter_and_the_pet_stays_an_ordinary_window():
    assert pet_native("linux") is None
