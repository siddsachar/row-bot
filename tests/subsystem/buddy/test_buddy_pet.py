"""The desktop pet's decisions: where it sits, when it follows, how it moves, what takes clicks."""

import base64

import pytest

from row_bot.buddy import pet
from row_bot.buddy.overlay import ScreenArea

SCREENS = [ScreenArea(0, 0, 1920, 1040)]  # a taskbar below 1040


def window(id: int, x: float, y: float, width: float = 900, height: float = 600) -> pet.OtherWindow:
    return pet.OtherWindow(id, pet.Rect(x, y, width, height))


def test_floor_is_the_work_area_bottom_of_the_screen_under_buddy():
    x, y = pet.floor_position(1900, SCREENS + [ScreenArea(1920, 0, 1280, 984)])
    assert (x, y) == (1920, 984 - pet.PET_HEIGHT + pet.FOOT)
    assert pet.floor_position(-50, SCREENS) == (0, 1040 - pet.PET_HEIGHT + pet.FOOT)


def test_the_window_in_use_is_the_frontmost_and_none_while_row_bot_itself_is_in_front():
    yours, behind = window(3, 100, 300), window(4, 500, 200)
    assert pet.window_in_use([(77, yours), (88, behind)], own_pid=10) == yours
    # Row-Bot's own window in front: not another app's window behind it.
    assert pet.window_in_use([(10, window(1, 0, 0)), (77, yours)], own_pid=10) is None
    assert pet.window_in_use([], own_pid=10) is None


def test_buddy_perches_on_a_wide_visible_top_edge_only():
    spot = pet.perch_position(pet.Rect(300, 400, 900, 500), 250, SCREENS)
    assert spot == (308, 400 - pet.PET_HEIGHT + pet.FOOT)
    assert pet.perch_position(pet.Rect(300, 400, 200, 500), 250, SCREENS) is None  # too narrow
    assert pet.perch_position(pet.Rect(0, 0, 1920, 1040), 250, SCREENS) is None  # maximised: no edge above it
    assert pet.perch_position(pet.Rect(300, 1020, 900, 500), 250, SCREENS) is None  # at the floor


def test_a_drop_on_a_title_bar_perches_and_a_drop_elsewhere_pauses_following():
    windows = [window(7, 300, 400), window(8, 200, 100)]
    assert pet.window_under(500, 410, windows).id == 7
    assert pet.window_under(500, 300, windows) is None
    follower = pet.Follower()
    follower.dropped(None, 100)
    assert not follower.following and follower.perched_on is None
    follower.dropped(windows[0], 500)
    assert follower.following and follower.perched_on == 7 and follower.offset_x == 200


def decide(follower, now, foreground, windows, pet_at=(500, 234), *, busy=False, enabled=True):
    return follower.decide(now=now, enabled=enabled, foreground=foreground,
                           windows={item.id: item for item in windows}, pet=pet_at, screens=SCREENS, busy=busy)


def test_following_waits_for_three_seconds_of_use_and_twenty_between_hops():
    a, b, c = window(1, 300, 400), window(2, 100, 200), window(3, 800, 300)
    follower = pet.Follower()
    follower.dropped(a, 500)
    windows = [a, b, c]
    assert decide(follower, 0.0, b, windows) is None
    assert decide(follower, 2.9, b, windows) is None
    kind, spot = decide(follower, 3.1, b, windows)
    assert kind == "hop" and follower.perched_on == 2 and spot[1] == 200 - pet.PET_HEIGHT + pet.FOOT
    pet_at = spot
    assert decide(follower, 4.0, c, windows, pet_at) is None
    assert decide(follower, 8.0, c, windows, pet_at) is None  # dwelled, but inside the cooldown
    assert decide(follower, 23.2, c, windows, pet_at)[0] == "hop"


def test_no_following_while_busy_disabled_or_paused():
    a, b = window(1, 300, 400), window(2, 100, 200)
    for options in ({"busy": True}, {"enabled": False}):
        follower = pet.Follower()
        follower.dropped(a, 500)
        assert decide(follower, 0, b, [a, b], **options) is None
        assert decide(follower, 10, b, [a, b], **options) is None
    paused = pet.Follower()
    paused.dropped(None, 500)
    assert decide(paused, 0, b, [a, b]) is None and decide(paused, 10, b, [a, b]) is None


def test_buddy_rides_along_with_its_window_and_falls_when_it_goes():
    a = window(1, 300, 400)
    follower = pet.Follower()
    follower.dropped(a, 500)
    moved = window(1, 360, 380)
    kind, spot = decide(follower, 1, moved, [moved], pet_at=(500, 234))
    assert kind == "ride" and spot == (560, 380 - pet.PET_HEIGHT + pet.FOOT)
    maximised = window(1, 0, 0, 1920, 1040)
    kind, spot = decide(follower, 2, maximised, [maximised], pet_at=spot)
    assert kind == "fall" and spot[1] == 1040 - pet.PET_HEIGHT + pet.FOOT and follower.perched_on is None
    follower.dropped(a, 500)
    assert decide(follower, 3, None, [], pet_at=(500, 234))[0] == "fall"  # closed


def test_moves_arc_for_hops_accelerate_for_falls_and_end_on_target():
    hop = pet.hop((100, 500), (900, 300), now=10.0)
    middle = hop.at(10.0 + hop.duration / 2)
    assert middle[1] < 300  # above both ends: an arc
    assert hop.at(10.0 + hop.duration) == (900, 300) and hop.done(10.0 + hop.duration)
    fall = pet.fall((100, 100), (100, 900), now=0.0)
    quarter, half = fall.at(fall.duration / 4)[1] - 100, fall.at(fall.duration / 2)[1] - 100
    assert half > quarter * 3  # gravity
    assert fall.at(fall.duration) == (100, 900)


def mask_payload(**changes):
    cells = bytearray(pet.MASK_CELLS * pet.MASK_CELLS // 8)
    cells[(12 * pet.MASK_CELLS + 12) >> 3] |= 0x80 >> ((12 * pet.MASK_CELLS + 12) & 7)  # the middle cell
    payload = {"action": "hit", "pet": [0, 0, 168, 176], "cells": base64.b64encode(bytes(cells)).decode()}
    payload.update(changes)
    return payload


def test_only_buddys_silhouette_and_open_bubble_take_clicks():
    mask = pet.parse_hit_mask(mask_payload(bubble=[180, 0, 380, 230]))
    assert mask.hit(85, 90)  # the middle of Buddy
    assert not mask.hit(4, 4)  # transparent corner of its box: passes through
    assert mask.hit(300, 100)  # the bubble
    assert not mask.hit(170, 200)


@pytest.mark.parametrize("changes", [
    {"cells": "not base64!"},
    {"cells": base64.b64encode(b"short").decode()},
    {"pet": [0, 0, -1, 10]},
    {"pet": [0, 0, float("inf"), 10]},
    {"pet": [0, 0, 10]},
    {"pet": [0, 0, True, 10]},
    {"bubble": [0, 0, 99999, 10]},
    {"extra": 1},
])
def test_malformed_hit_masks_are_refused(changes):
    with pytest.raises(ValueError, match="invalid_hit_mask"):
        pet.parse_hit_mask(mask_payload(**changes))
