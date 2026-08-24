"""Parse Tesla fleet-telemetry HTTP-dispatcher JSON."""

from cloud.tesla_aladdin_garage.telemetry import extract_location_fixes

VIN = "7SAYGAEE2TF605512"


def test_plain_lat_lon():
    fixes = extract_location_fixes({"latitude": 29.46, "longitude": -95.51, "vin": VIN})
    assert len(fixes) == 1
    assert fixes[0]["latitude"] == 29.46
    assert fixes[0]["longitude"] == -95.51
    assert fixes[0]["vin"] == VIN


def test_data_map_location():
    fixes = extract_location_fixes(
        {"vin": VIN, "data": {"Location": {"latitude": 1.0, "longitude": 2.0}}}
    )
    assert fixes[0]["latitude"] == 1.0
    assert fixes[0]["longitude"] == 2.0


def test_location_value_protobuf_json():
    fixes = extract_location_fixes(
        {
            "vin": VIN,
            "data": [
                {
                    "key": "Location",
                    "value": {"locationValue": {"latitude": 3.0, "longitude": 4.0}},
                }
            ],
        }
    )
    assert fixes[0]["latitude"] == 3.0
    assert fixes[0]["longitude"] == 4.0


def test_wrong_vin_dropped():
    fixes = extract_location_fixes(
        {"vin": "OTHER", "latitude": 1, "longitude": 2},
        expected_vin=VIN,
    )
    assert fixes == []


def test_list_of_payloads():
    fixes = extract_location_fixes(
        [
            {"latitude": 1.0, "longitude": 2.0, "vin": VIN},
            {"latitude": 3.0, "longitude": 4.0, "vin": VIN},
        ]
    )
    assert len(fixes) == 2
