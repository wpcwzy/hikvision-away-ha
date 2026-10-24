"""Minimal synchronous client for the Hikvision ISAPI endpoints used here.

The integration only needs three things from the NVR:

* device information (for the Home Assistant device registry),
* the list of camera channels (for the config flow), and
* the field-detection event trigger of one channel.

On Hikvision NVRs the perimeter-protection / region-intrusion event is called
``fielddetection``.  Its "notify surveillance center" link (上传中心 in the web
UI) is represented by a ``notificationMethod`` value of ``center`` in the
``EventTriggerNotificationList`` of ``/ISAPI/Event/triggers/fielddetection-<n>``.

Everything in this module is synchronous and is executed in Home Assistant's
executor thread.  It deliberately has no Home Assistant imports so it can be
unit tested on its own.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

DEVICE_INFO_PATH = "/ISAPI/System/deviceInfo"
INPUT_PROXY_CHANNELS_PATH = "/ISAPI/ContentMgmt/InputProxy/channels"
VIDEO_INPUT_CHANNELS_PATH = "/ISAPI/System/Video/inputs/channels"
FIELD_DETECTION_TRIGGER_PATH = "/ISAPI/Event/triggers/fielddetection-{channel}"

XML_CONTENT_TYPE = "application/xml; charset=UTF-8"

_NOTIFICATION_RE = re.compile(
    r"<EventTriggerNotification\b[^>]*>.*?</EventTriggerNotification\s*>",
    re.DOTALL,
)
_CENTER_METHOD_RE = re.compile(
    r"<notificationMethod>\s*center\s*</notificationMethod>",
    re.IGNORECASE,
)
_CLOSE_NOTIFICATION_LIST_RE = re.compile(r"</EventTriggerNotificationList\s*>")
_OPEN_NOTIFICATION_LIST_RE = re.compile(
    r"<EventTriggerNotificationList\b([^>]*?)(/?)>",
    re.DOTALL,
)
_RESPONSE_STATUS = "ResponseStatus"
_CENTER_NOTIFICATION = (
    "<EventTriggerNotification>\n"
    "<id>center</id>\n"
    "<notificationMethod>center</notificationMethod>\n"
    "</EventTriggerNotification>\n"
)


class HikvisionError(Exception):
    """Base class for errors raised by the ISAPI client."""


class HikvisionConnectionError(HikvisionError):
    """The NVR could not be reached."""


class HikvisionAuthError(HikvisionError):
    """The NVR rejected the credentials."""


class HikvisionResponseError(HikvisionError):
    """The NVR returned an error response."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        status_code: str | None = None,
        status_string: str | None = None,
        sub_status_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.status_code = status_code
        self.status_string = status_string
        self.sub_status_code = sub_status_code


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Information shown in the Home Assistant device registry."""

    name: str
    model: str
    serial_number: str
    firmware_version: str
    device_type: str


@dataclass(frozen=True, slots=True)
class Channel:
    """A camera channel on the NVR."""

    id: int
    name: str
    ip_address: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LinkageState:
    """State of the field-detection event linkage of one channel."""

    upload_center: bool
    notification_methods: tuple[str, ...]
    raw_xml: str


def local_name(tag: str) -> str:
    """Return an XML tag without its namespace."""
    return tag.rsplit("}", 1)[-1]


def _first_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def _child_text(element: ET.Element, name: str, default: str = "") -> str:
    child = _first_child(element, name)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _parse_xml(text: str) -> ET.Element:
    if not text or not text.strip():
        raise HikvisionResponseError("The NVR returned an empty response.")
    try:
        return ET.fromstring(text)
    except ET.ParseError as err:
        raise HikvisionResponseError(f"The NVR returned invalid XML: {err}") from err


def _response_status_values(root: ET.Element) -> tuple[str, str, str]:
    return (
        _child_text(root, "statusCode"),
        _child_text(root, "statusString"),
        _child_text(root, "subStatusCode"),
    )


def _raise_for_isapi_status(root: ET.Element) -> None:
    """Raise if *root* is a failing Hikvision ResponseStatus document."""
    if local_name(root.tag) != _RESPONSE_STATUS:
        return
    status_code, status_string, sub_status_code = _response_status_values(root)
    if status_code in ("", "1"):
        return
    message = status_string or "ISAPI request failed"
    if status_code:
        message += f" (statusCode={status_code}"
        if sub_status_code:
            message += f", subStatusCode={sub_status_code}"
        message += ")"
    raise HikvisionResponseError(
        message,
        status_code=status_code or None,
        status_string=status_string or None,
        sub_status_code=sub_status_code or None,
    )


def parse_linkage_state(xml_text: str) -> LinkageState:
    """Parse a field-detection EventTrigger document.

    ``upload_center`` is true when a ``center`` notification exists.  The
    other notification methods (record, audioOut, whiteLightOut, ...) are
    returned as well so callers can preserve them.
    """
    root = _parse_xml(xml_text)
    _raise_for_isapi_status(root)

    if local_name(root.tag) != "EventTrigger":
        raise HikvisionResponseError(
            f"Unexpected ISAPI document: <{local_name(root.tag)}>."
        )

    notification_list = _first_child(root, "EventTriggerNotificationList")
    if notification_list is None:
        raise HikvisionResponseError(
            "The channel does not expose an EventTriggerNotificationList."
        )

    methods: list[str] = []
    for notification in notification_list:
        if local_name(notification.tag) != "EventTriggerNotification":
            continue
        method = _child_text(notification, "notificationMethod")
        if method:
            methods.append(method)

    return LinkageState(
        upload_center="center" in methods,
        notification_methods=tuple(methods),
        raw_xml=xml_text,
    )


def update_upload_center_xml(xml_text: str, enabled: bool) -> str:
    """Return *xml_text* with the upload-center notification enabled/disabled.

    The raw XML is modified instead of being re-serialised by ElementTree.
    Hikvision devices are strict about XML namespace prefixes and this keeps
    every other part of the document byte-for-byte intact.
    """
    state = parse_linkage_state(xml_text)

    if state.upload_center is enabled:
        return xml_text

    if not enabled:
        matches = [
            match
            for match in _NOTIFICATION_RE.finditer(xml_text)
            if _CENTER_METHOD_RE.search(match.group(0))
        ]
        if not matches:
            return xml_text

        updated = xml_text
        for match in reversed(matches):
            start, end = match.span()
            # Remove the indentation on the notification's own line.
            line_start = updated.rfind("\n", 0, start) + 1
            if not updated[line_start:start].strip():
                start = line_start
            # Remove the trailing newline/indentation too, so no blank line is
            # left behind in the XML document.
            while end < len(updated) and updated[end] in " \t\r\n":
                end += 1
            updated = updated[:start] + updated[end:]
    else:
        block = _CENTER_NOTIFICATION
        close_match = _CLOSE_NOTIFICATION_LIST_RE.search(xml_text)
        if close_match is not None:
            insert_at = close_match.start()
            if not xml_text[:insert_at].endswith("\n"):
                block = "\n" + block
            updated = xml_text[:insert_at] + block + xml_text[insert_at:]
        else:
            open_match = _OPEN_NOTIFICATION_LIST_RE.search(xml_text)
            if open_match is None:
                raise HikvisionResponseError(
                    "The channel does not expose an EventTriggerNotificationList."
                )
            open_tag = f"<EventTriggerNotificationList{open_match.group(1)}>"
            replacement = f"{open_tag}\n{block}</EventTriggerNotificationList>"
            updated = (
                xml_text[: open_match.start()]
                + replacement
                + xml_text[open_match.end() :]
            )

    # Sanity-check the result before it is sent to the NVR.  This also proves
    # that all pre-existing linkage options were preserved.
    new_state = parse_linkage_state(updated)
    if new_state.upload_center is not enabled:
        raise HikvisionError(
            "Could not update the upload-center notification in the NVR XML."
        )
    if [m for m in new_state.notification_methods if m != "center"] != [
        m for m in state.notification_methods if m != "center"
    ]:
        raise HikvisionError(
            "Refusing to write XML that would change other event linkage options."
        )
    return updated


class HikvisionIsapiClient:
    """Small HTTP Digest client for a Hikvision NVR."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 80,
        use_ssl: bool = False,
        verify_ssl: bool = False,
        timeout: int = 10,
        session: requests.Session | None = None,
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.scheme = "https" if use_ssl else "http"
        self._session = session or requests.Session()
        self._session.auth = HTTPDigestAuth(username, password)
        self._session.headers.update(
            {
                "Accept": "application/xml",
                "User-Agent": "HomeAssistant-HikvisionAway/1.0",
            }
        )

    @property
    def base_url(self) -> str:
        """Return the base URL of the NVR."""
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default_port = 443 if self.use_ssl else 80
        if self.port == default_port:
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def _request(self, method: str, path: str, *, body: str | None = None) -> str:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = XML_CONTENT_TYPE

        try:
            response = self._session.request(
                method,
                url,
                data=body.encode("utf-8") if body is not None else None,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as err:
            raise HikvisionConnectionError(
                f"Timed out while connecting to {self.base_url}."
            ) from err
        except requests.exceptions.SSLError as err:
            raise HikvisionConnectionError(
                f"TLS error while connecting to {self.base_url}: {err}"
            ) from err
        except requests.exceptions.RequestException as err:
            raise HikvisionConnectionError(
                f"Could not connect to {self.base_url}: {err}"
            ) from err

        text = _decode_response(response)

        if response.status_code == 401:
            # Some firmware only offers Basic authentication.  Requests has
            # already performed the Digest handshake at this point, so this is
            # a safe, one-time fallback when the server asks for Basic.
            auth_header = response.headers.get("WWW-Authenticate", "")
            if (
                isinstance(self._session.auth, HTTPDigestAuth)
                and "basic" in auth_header.lower()
                and "digest" not in auth_header.lower()
            ):
                self._session.auth = HTTPBasicAuth(self.username, self.password)
                return self._request(method, path, body=body)
            raise HikvisionAuthError("Authentication with the NVR failed (HTTP 401).")

        if response.status_code >= 400:
            raise _make_response_error(response, text, method, path)

        # Some ISAPI endpoints return HTTP 200 with a ResponseStatus error.
        try:
            root = ET.fromstring(text) if text.strip() else None
        except ET.ParseError:
            root = None
        if root is not None:
            _raise_for_isapi_status(root)

        return text

    def get_device_info(self) -> DeviceInfo:
        """Return basic information about the NVR."""
        root = _parse_xml(self._request("GET", DEVICE_INFO_PATH))
        _raise_for_isapi_status(root)
        if local_name(root.tag) != "DeviceInfo":
            raise HikvisionResponseError(
                f"Unexpected device info document: <{local_name(root.tag)}>."
            )
        serial = _child_text(root, "serialNumber") or _child_text(root, "deviceID")
        return DeviceInfo(
            name=_child_text(root, "deviceName") or "Hikvision NVR",
            model=_child_text(root, "model") or "Hikvision NVR",
            serial_number=serial,
            firmware_version=_child_text(root, "firmwareVersion"),
            device_type=_child_text(root, "deviceType"),
        )

    def get_channels(self) -> list[Channel]:
        """Return the camera channels configured on the NVR.

        The InputProxy list is used for NVRs with IP cameras.  A small fallback
        covers devices that only expose the VideoInput list.
        """
        last_error: HikvisionError | None = None
        for path in (INPUT_PROXY_CHANNELS_PATH, VIDEO_INPUT_CHANNELS_PATH):
            try:
                text = self._request("GET", path)
                root = _parse_xml(text)
                _raise_for_isapi_status(root)
                if local_name(root.tag) not in (
                    "InputProxyChannelList",
                    "VideoInputChannelList",
                ):
                    raise HikvisionResponseError(
                        f"Unexpected channel-list document: <{local_name(root.tag)}>."
                    )
                return _parse_channels(root)
            except HikvisionResponseError as err:
                last_error = err
                continue
        if last_error is not None:
            raise last_error
        return []

    def get_linkage_state(self, channel: int) -> LinkageState:
        """Read the field-detection linkage state for *channel*."""
        text = self._request(
            "GET", FIELD_DETECTION_TRIGGER_PATH.format(channel=channel)
        )
        return parse_linkage_state(text)

    def set_upload_center(self, channel: int, enabled: bool) -> bool:
        """Enable or disable the upload-center link for *channel*.

        Returns ``True`` when the NVR was changed and ``False`` when it already
        had the requested state.
        """
        path = FIELD_DETECTION_TRIGGER_PATH.format(channel=channel)
        state = parse_linkage_state(self._request("GET", path))
        if state.upload_center is enabled:
            return False

        updated_xml = update_upload_center_xml(state.raw_xml, enabled)
        response_text = self._request("PUT", path, body=updated_xml)
        try:
            root = ET.fromstring(response_text) if response_text.strip() else None
        except ET.ParseError:
            root = None
        if root is None:
            # An empty 200 response is tolerated; Home Assistant verifies the
            # new state with a follow-up GET.
            return True
        if local_name(root.tag) != _RESPONSE_STATUS:
            raise HikvisionResponseError(
                f"Unexpected response to update: <{local_name(root.tag)}>."
            )
        status_code, status_string, sub_status_code = _response_status_values(root)
        if status_code != "1":
            raise HikvisionResponseError(
                status_string or "The NVR rejected the update.",
                status_code=status_code or None,
                status_string=status_string or None,
                sub_status_code=sub_status_code or None,
            )
        return True


def _parse_channels(root: ET.Element) -> list[Channel]:
    channels: list[Channel] = []
    for node in root:
        if local_name(node.tag) not in ("InputProxyChannel", "VideoInputChannel"):
            continue
        raw_id = _child_text(node, "id") or _child_text(node, "channelID")
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        source = _first_child(node, "sourceInputPortDescriptor")
        ip_address = _child_text(source, "ipAddress") if source is not None else ""
        model = _child_text(source, "model") if source is not None else ""
        channels.append(
            Channel(
                id=channel_id,
                name=_child_text(node, "name") or f"Channel {channel_id}",
                ip_address=ip_address or None,
                model=model or None,
            )
        )
    return sorted(channels, key=lambda item: item.id)


def _decode_response(response: requests.Response) -> str:
    """Decode an ISAPI response as UTF-8.

    ``requests`` guesses ISO-8859-1 for many XML responses without a charset,
    which corrupts Chinese channel names.  Hikvision ISAPI documents are UTF-8
    (or GB18030 on very old firmware), so give those encodings priority.
    """
    content = response.content
    if not content:
        return ""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _make_response_error(
    response: requests.Response,
    text: str,
    method: str,
    path: str,
) -> HikvisionResponseError:
    status_code = ""
    status_string = ""
    sub_status_code = ""
    try:
        root = ET.fromstring(text) if text.strip() else None
    except ET.ParseError:
        root = None
    if root is not None and local_name(root.tag) == _RESPONSE_STATUS:
        status_code, status_string, sub_status_code = _response_status_values(root)
    message = f"ISAPI {method} {path} failed with HTTP {response.status_code}"
    if status_string:
        message += f": {status_string}"
    if status_code and status_code != "1":
        message += f" (statusCode={status_code}"
        if sub_status_code:
            message += f", subStatusCode={sub_status_code}"
        message += ")"
    return HikvisionResponseError(
        message,
        http_status=response.status_code,
        status_code=status_code or None,
        status_string=status_string or None,
        sub_status_code=sub_status_code or None,
    )
