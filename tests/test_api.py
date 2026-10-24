"""Unit tests for the pure-Python Hikvision ISAPI client."""

from __future__ import annotations

import pytest

from custom_components.hikvision_away.api import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionIsapiClient,
    HikvisionResponseError,
    parse_linkage_state,
    update_upload_center_xml,
)

DEVICE_INFO_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">
<deviceName>网络硬盘录像机</deviceName>
<deviceID>test-device-id</deviceID>
<model>DS-7808N-TEST</model>
<serialNumber>DS-7808N-TEST-SERIAL</serialNumber>
<firmwareVersion>V4.85.010</firmwareVersion>
<deviceType>NVR</deviceType>
</DeviceInfo>
"""

CHANNELS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
<InputProxyChannel>
<id>1</id>
<name>前院</name>
<sourceInputPortDescriptor>
<ipAddress>192.0.2.10</ipAddress>
<model>DS-2CD3T47</model>
</sourceInputPortDescriptor>
</InputProxyChannel>
<InputProxyChannel>
<id>2</id>
<name>后院</name>
<sourceInputPortDescriptor>
<ipAddress>192.0.2.11</ipAddress>
<model>DS-2CD3T47</model>
</sourceInputPortDescriptor>
</InputProxyChannel>
</InputProxyChannelList>
"""

TRIGGER_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<EventTrigger version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
<id>fielddetection-1</id>
<eventType>fielddetection</eventType>
<dynVideoInputChannelID>1</dynVideoInputChannelID>
<EventTriggerNotificationList version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
{notifications}</EventTriggerNotificationList>
</EventTrigger>
"""

RECORD_NOTIFICATION = """<EventTriggerNotification>
<id>record-1</id>
<notificationMethod>record</notificationMethod>
<dynVideoInputID>1</dynVideoInputID>
</EventTriggerNotification>
"""

CENTER_NOTIFICATION = """<EventTriggerNotification>
<id>center</id>
<notificationMethod>center</notificationMethod>
</EventTriggerNotification>
"""

AUDIO_NOTIFICATION = """<EventTriggerNotification>
<id>audioOut-1</id>
<notificationMethod>audioOut</notificationMethod>
<lightAudioOutID>1</lightAudioOutID>
</EventTriggerNotification>
"""

SUCCESS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<ResponseStatus xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
<statusString>OK</statusString>
<subStatusCode>ok</subStatusCode>
</ResponseStatus>
"""


def trigger_xml(*notifications: str) -> str:
    body = "".join(notifications)
    return TRIGGER_TEMPLATE.format(notifications=body)


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.auth: object | None = None
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def make_client(session: FakeSession) -> HikvisionIsapiClient:
    return HikvisionIsapiClient(
        "192.0.2.1",
        "admin",
        "secret",
        session=session,  # type: ignore[arg-type]
    )


def test_parse_linkage_state_enabled() -> None:
    state = parse_linkage_state(trigger_xml(RECORD_NOTIFICATION, CENTER_NOTIFICATION))
    assert state.upload_center is True
    assert state.notification_methods == ("record", "center")


def test_parse_linkage_state_disabled() -> None:
    state = parse_linkage_state(trigger_xml(RECORD_NOTIFICATION))
    assert state.upload_center is False
    assert state.notification_methods == ("record",)


def test_update_upload_center_adds_center_and_preserves_other_methods() -> None:
    original = trigger_xml(RECORD_NOTIFICATION, AUDIO_NOTIFICATION)
    updated = update_upload_center_xml(original, True)

    state = parse_linkage_state(updated)
    assert state.upload_center is True
    assert state.notification_methods.count("center") == 1
    assert state.notification_methods == ("record", "audioOut", "center")
    # The unrelated blocks must still be present verbatim.
    assert "<id>record-1</id>" in updated
    assert "<id>audioOut-1</id>" in updated


def test_update_upload_center_removes_only_center() -> None:
    original = trigger_xml(RECORD_NOTIFICATION, CENTER_NOTIFICATION, AUDIO_NOTIFICATION)
    updated = update_upload_center_xml(original, False)

    state = parse_linkage_state(updated)
    assert state.upload_center is False
    assert state.notification_methods == ("record", "audioOut")
    assert "<notificationMethod>center</notificationMethod>" not in updated


def test_update_upload_center_is_idempotent() -> None:
    with_center = trigger_xml(RECORD_NOTIFICATION, CENTER_NOTIFICATION)
    without_center = trigger_xml(RECORD_NOTIFICATION)
    assert update_upload_center_xml(with_center, True) == with_center
    assert update_upload_center_xml(without_center, False) == without_center


def test_update_upload_center_handles_self_closing_notification_list() -> None:
    original = (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<EventTrigger version="2.0" '
        'xmlns="http://www.isapi.org/ver20/XMLSchema">\n'
        "<id>fielddetection-1</id>\n"
        "<eventType>fielddetection</eventType>\n"
        "<dynVideoInputChannelID>1</dynVideoInputChannelID>\n"
        '<EventTriggerNotificationList version="2.0" '
        'xmlns="http://www.isapi.org/ver20/XMLSchema"/>\n'
        "</EventTrigger>"
    )
    updated = update_upload_center_xml(original, True)
    state = parse_linkage_state(updated)
    assert state.upload_center is True
    assert state.notification_methods == ("center",)


def test_update_upload_center_rejects_unexpected_document() -> None:
    with pytest.raises(HikvisionResponseError):
        update_upload_center_xml("<not-an-event-trigger/>", True)


def test_get_device_info() -> None:
    session = FakeSession([FakeResponse(text=DEVICE_INFO_XML)])
    client = make_client(session)
    device = client.get_device_info()

    assert device.name == "网络硬盘录像机"
    assert device.model == "DS-7808N-TEST"
    assert device.serial_number == "DS-7808N-TEST-SERIAL"
    assert device.firmware_version == "V4.85.010"
    assert session.calls[0]["method"] == "GET"
    assert str(session.calls[0]["url"]).endswith("/ISAPI/System/deviceInfo")


def test_get_channels_uses_utf8_names() -> None:
    session = FakeSession([FakeResponse(text=CHANNELS_XML)])
    client = make_client(session)
    channels = client.get_channels()

    assert [channel.id for channel in channels] == [1, 2]
    assert [channel.name for channel in channels] == ["前院", "后院"]
    assert channels[0].ip_address == "192.0.2.10"


def test_auth_error() -> None:
    session = FakeSession(
        [FakeResponse(401, headers={"WWW-Authenticate": 'Digest realm="test"'})]
    )
    client = make_client(session)
    with pytest.raises(HikvisionAuthError):
        client.get_device_info()


def test_digest_client_falls_back_to_basic_auth() -> None:
    session = FakeSession(
        [
            FakeResponse(401, headers={"WWW-Authenticate": 'Basic realm="test"'}),
            FakeResponse(text=DEVICE_INFO_XML),
        ]
    )
    client = make_client(session)
    device = client.get_device_info()

    assert device.model == "DS-7808N-TEST"
    assert len(session.calls) == 2
    assert session.auth.__class__.__name__ == "HTTPBasicAuth"


def test_connection_error() -> None:
    class BrokenSession(FakeSession):
        def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            import requests

            raise requests.exceptions.ConnectionError("boom")

    session = BrokenSession([])
    client = make_client(session)
    with pytest.raises(HikvisionConnectionError):
        client.get_device_info()


def test_set_upload_center_writes_expected_document() -> None:
    session = FakeSession(
        [
            FakeResponse(text=trigger_xml(RECORD_NOTIFICATION)),
            FakeResponse(text=SUCCESS_XML),
        ]
    )
    client = make_client(session)
    changed = client.set_upload_center(1, True)

    assert changed is True
    assert [call["method"] for call in session.calls] == ["GET", "PUT"]
    put_call = session.calls[1]
    assert put_call["kwargs"]["headers"]["Content-Type"] == (
        "application/xml; charset=UTF-8"
    )
    body = put_call["kwargs"]["data"].decode("utf-8")
    assert "<notificationMethod>center</notificationMethod>" in body
    assert "<id>record-1</id>" in body


def test_set_upload_center_is_noop_when_already_enabled() -> None:
    session = FakeSession(
        [FakeResponse(text=trigger_xml(RECORD_NOTIFICATION, CENTER_NOTIFICATION))]
    )
    client = make_client(session)
    changed = client.set_upload_center(1, True)

    assert changed is False
    assert [call["method"] for call in session.calls] == ["GET"]


def test_isapi_error_response() -> None:
    error_xml = """<?xml version="1.0" encoding="UTF-8" ?>
<ResponseStatus xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>4</statusCode>
<statusString>Invalid Operation</statusString>
<subStatusCode>notSupport</subStatusCode>
</ResponseStatus>
"""
    session = FakeSession([FakeResponse(200, text=error_xml)])
    client = make_client(session)
    with pytest.raises(HikvisionResponseError) as exc_info:
        client.get_linkage_state(1)
    assert exc_info.value.status_code == "4"
    assert exc_info.value.sub_status_code == "notSupport"
