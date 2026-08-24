"""Geofence hysteresis + first-sample skip."""

from cloud.tesla_aladdin_garage.geofence import Geofence, haversine_m

HOME = (29.464083, -95.517465)


def test_haversine_zero():
    assert haversine_m(*HOME, *HOME) < 0.01


def _at(meters_north: float) -> tuple[float, float]:
    return (HOME[0] + meters_north / 111320.0, HOME[1])


def test_first_sample_inside_is_not_enter():
    g = Geofence(*HOME, enter_m=400, hysteresis_m=80)
    assert g.observe(*HOME) == "inside"
    assert g.observe(*HOME) == "inside"


def test_enter_only_from_outside():
    g = Geofence(*HOME, enter_m=400, hysteresis_m=80)
    assert g.observe(*_at(800)) == "outside"
    assert g.observe(*_at(350)) == "enter"


def test_hysteresis_holds_until_exit_radius():
    g = Geofence(*HOME, enter_m=400, hysteresis_m=80)
    g.observe(*_at(800))
    g.observe(*_at(100))
    assert g.observe(*_at(450)) == "inside"
    assert g.observe(*_at(500)) == "exit"
