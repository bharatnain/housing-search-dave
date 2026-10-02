import inspect

import vet


def feat(borough, lon, lat):
    return {"properties": {"borough": borough}, "geometry": {"coordinates": [lon, lat]}}


def test_pick_feature_prefers_profile_borough_then_nearest():
    fs = [feat("Manhattan", -73.99, 40.74), feat("Brooklyn", -73.98, 40.67), feat("Brooklyn", -73.95, 40.71)]
    assert vet.pick_feature(fs)["properties"]["borough"] == "Manhattan"
    assert vet.pick_feature(fs, ["brooklyn"]) is fs[1]
    assert vet.pick_feature(fs, ["brooklyn"], (40.71, -73.95)) is fs[2]
    assert vet.pick_feature(fs, ["queens"]) is fs[0]   # no match in the profile: GeoSearch's best


def test_no_borough_default():
    assert "Manhattan" not in inspect.getsource(vet.vet)
