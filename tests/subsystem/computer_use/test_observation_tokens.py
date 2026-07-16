from __future__ import annotations

import pytest

from row_bot.computer_use.service import LeaseOwner, StaleObservationError


OWNER = LeaseOwner("thread", "generation", "task")


def _capture_calculator(service):
    service.acquire(OWNER, validate_context=False)
    windows = service.list_windows(OWNER, app="Calculator")
    observation = service.capture(windows[0]["target_id"], OWNER)
    return windows[0]["target_id"], observation


def test_target_ids_hide_driver_pid_and_observation_tokens_are_generation_bound(service) -> None:
    target_id, observation = _capture_calculator(service)
    assert "4242" not in target_id
    token = observation.elements[0].token
    service.invalidate_observation("test")
    with pytest.raises(StaleObservationError):
        service._current_element(token)


def test_out_of_bounds_coordinates_fail_before_driver_mutation(service, fake_transport) -> None:
    target_id, _ = _capture_calculator(service)
    before = len(fake_transport.calls)
    with pytest.raises(Exception, match="outside"):
        service.act("click", target_id, OWNER, x=9999, y=9999)
    assert len(fake_transport.calls) == before


def test_negative_monitor_origin_and_dpi_remain_target_metadata_but_actions_use_window_pixels(service, fake_transport) -> None:
    target_id, observation = _capture_calculator(service)
    assert observation.target.bounds[0] == -100
    assert observation.scale_factor == 1.25
    service.act("click", target_id, OWNER, x=0, y=0, expected_effect="reversible canvas selection")
    click_args = next(args for name, args in fake_transport.calls if name == "click")
    assert click_args["x"] == 0 and click_args["y"] == 0
    assert click_args["window_id"] == 101
