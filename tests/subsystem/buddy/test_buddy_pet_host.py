"""The desktop pet's live loop with a fake window system and clock."""

import base64

import pytest

from row_bot.buddy import pet
from row_bot.buddy.overlay import ScreenArea
from row_bot.buddy.pet_host import BUBBLE_WIDTH, GAP, PetController

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class FakeNative:
    def __init__(self):
        self.cursor_at = (0.0, 0.0)
        self.placed: list[pet.Rect] = []
        self.through: list[bool] = []
        self.front: pet.OtherWindow | None = None
        self.others: list[pet.OtherWindow] = []

    def prepare(self, window):
        return True

    def place(self, window, frame):
        self.placed.append(frame)
        return True

    def set_click_through(self, window, through):
        self.through.append(through)
        return True

    def cursor(self):
        return self.cursor_at

    def frame(self, window):
        return self.placed[-1] if self.placed else pet.Rect(0, 0, pet.PET_WIDTH, pet.PET_HEIGHT)

    def scale(self, window):
        return 1.0

    def windows(self):
        return list(self.others)

    def foreground(self):
        return self.front


class FakeHost:
    def __init__(self, screens):
        self.screens = screens
        self.rests: list[tuple[float, float]] = []
        self.config = {}

    def screen_areas(self):
        return self.screens

    def _factor(self):
        return 1.0

    def _on_ui(self, function, *args):
        function(*args)

    def _load_config(self):
        return self.config

    def pet_rest(self, x, y):
        self.rests.append((x, y))


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


SCREEN = ScreenArea(0, 0, 1920, 1040)
FLOOR_Y = 1040 - pet.PET_HEIGHT + pet.FOOT


def controller(x=400.0, y=300.0, *, drop=True):
    native, host, clock = FakeNative(), FakeHost([SCREEN]), Clock()
    control = PetController(host, native, clock=clock, threaded=False)
    control.attach(object(), x, y, drop=drop)
    return control, native, host, clock


def run(control, clock, seconds):
    for _ in range(round(seconds * 30)):
        clock.now += 1 / 30
        control.tick(clock.now)


def test_a_torn_off_buddy_falls_to_the_floor_and_rests_there():
    control, native, host, clock = controller()
    run(control, clock, 1.5)
    assert control.position() == (400, FLOOR_Y)
    assert native.placed[-1] == pet.Rect(400, FLOOR_Y, pet.PET_WIDTH, pet.PET_HEIGHT)
    assert control.state()["landed"] == 1 and host.rests[-1] == (400, FLOOR_Y)


def test_a_popped_out_buddy_goes_to_sit_on_your_window_until_you_put_it_on_the_floor():
    control, native, host, clock = controller()
    run(control, clock, 1.5)
    assert control.position() == (400, FLOOR_Y)
    yours = pet.OtherWindow(4, pet.Rect(300, 500, 900, 400))
    native.front, native.others = yours, [yours]
    run(control, clock, 5)
    assert control.state()["perched"] and control.position()[1] == 500 - pet.PET_HEIGHT + pet.FOOT
    control.drag("start")
    control.moved(400, 700)  # down to the floor, by hand
    control.drag("end")
    run(control, clock, 1.5)
    other = pet.OtherWindow(5, pet.Rect(900, 300, 900, 400))
    native.front, native.others = other, [yours, other]
    run(control, clock, 30)
    assert control.position() == (400, FLOOR_Y) and not control.state()["perched"]


def test_a_drop_on_a_title_bar_perches_and_it_rides_along():
    control, native, host, clock = controller(drop=False)
    native.others = [pet.OtherWindow(9, pet.Rect(300, 500, 900, 400))]
    control.drag("start")
    control.moved(500, 500 - pet.PET_HEIGHT + 20)  # feet near the title bar
    control.drag("end")
    run(control, clock, 1.0)
    assert control.position()[1] == 500 - pet.PET_HEIGHT + pet.FOOT and control.state()["perched"]
    native.others = [pet.OtherWindow(9, pet.Rect(340, 450, 900, 400))]  # the window moved
    run(control, clock, 0.3)
    assert control.position() == (540, 450 - pet.PET_HEIGHT + pet.FOOT)


def test_only_the_silhouette_takes_clicks_and_a_drag_keeps_them():
    control, native, host, clock = controller(drop=False)
    cells = bytearray(pet.MASK_CELLS * pet.MASK_CELLS // 8)
    middle = 12 * pet.MASK_CELLS + 12
    cells[middle >> 3] |= 0x80 >> (middle & 7)
    control.hit(pet.parse_hit_mask({"pet": [0, 0, pet.PET_WIDTH, pet.PET_HEIGHT],
                                    "cells": base64.b64encode(bytes(cells)).decode()}))
    native.cursor_at = (400 + 2, 300 + 2)  # a transparent corner
    run(control, clock, 0.1)
    assert native.through[-1] is True
    native.cursor_at = (400 + pet.PET_WIDTH / 2, 300 + pet.PET_HEIGHT / 2)
    run(control, clock, 0.1)
    assert native.through[-1] is False
    control.drag("start")
    native.cursor_at = (400 + 2, 300 + 2)
    run(control, clock, 0.1)
    assert native.through[-1] is False
    state = control.state()
    assert state["cursor"]["x"] == pytest.approx(2) and state["cursor"]["age"] >= 0


def test_the_bubble_opens_toward_the_side_with_room_and_buddy_stays_put():
    control, native, host, clock = controller(400, FLOOR_Y, drop=False)
    geometry = control.layout(True)
    assert geometry["side"] == "right" and geometry["petX"] == 0
    assert native.placed[-1].x == 400 and native.placed[-1].width == pet.PET_WIDTH + GAP + BUBBLE_WIDTH
    assert control.position() == (400, FLOOR_Y)
    control.layout(False)
    near_edge, native2, _, _ = controller(1700, FLOOR_Y, drop=False)
    geometry = near_edge.layout(True)
    assert geometry["side"] == "left" and geometry["petX"] == GAP + BUBBLE_WIDTH
    assert native2.placed[-1].x == 1700 - GAP - BUBBLE_WIDTH and near_edge.position() == (1700, FLOOR_Y)


def test_it_follows_you_to_another_window_but_not_while_its_bubble_is_open():
    control, native, host, clock = controller(drop=False)
    perch = pet.OtherWindow(1, pet.Rect(200, 600, 900, 300))
    native.others = [perch]
    control.drag("start")
    control.moved(400, 600 - pet.PET_HEIGHT + 20)
    control.drag("end")
    run(control, clock, 1.0)
    other = pet.OtherWindow(2, pet.Rect(800, 200, 900, 500))
    native.front, native.others = other, [perch, other]
    control.layout(True)
    run(control, clock, 5)
    assert control.state()["perched"] and control.position()[1] == 600 - pet.PET_HEIGHT + pet.FOOT
    control.layout(False)
    run(control, clock, 5)
    assert control.position()[1] == 200 - pet.PET_HEIGHT + pet.FOOT
    host.config["follow_windows"] = False
    third = pet.OtherWindow(3, pet.Rect(100, 400, 900, 500))
    native.front, native.others = third, [other, third]
    run(control, clock, 30)
    assert control.position()[1] == 200 - pet.PET_HEIGHT + pet.FOOT  # following is off
