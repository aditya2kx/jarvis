from cloud.tesla_aladdin_garage.mqtt_forwarder import garage_body_from_mqtt

VIN = "7SAYGAEE2TF605512"


def test_official_mqtt_location_topic():
    body = garage_body_from_mqtt(
        f"telemetry/{VIN}/v/Location",
        b'{"latitude": 29.464083, "longitude": -95.517465}',
        expected_vin=VIN,
    )
    assert body["vin"] == VIN
    loc = body["data"][0]["value"]["locationValue"]
    assert loc["latitude"] == 29.464083
    assert loc["longitude"] == -95.517465


def test_wrong_vin_dropped():
    assert (
        garage_body_from_mqtt(
            "telemetry/OTHER/v/Location",
            b'{"latitude": 1, "longitude": 2}',
            expected_vin=VIN,
        )
        is None
    )


def test_non_location_ignored():
    assert (
        garage_body_from_mqtt(
            f"telemetry/{VIN}/v/Speed",
            b"12.3",
            expected_vin=VIN,
        )
        is None
    )
