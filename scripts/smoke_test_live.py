#!/usr/bin/env python3
"""Live smoke test for the Hikvision NVR Away Arm integration.

This bypasses Home Assistant and talks directly to the NVR using the same
``api.py`` module that the integration uses.  It is useful for checking an NVR
before/after installing the custom component.

Usage:

    export HIKVISION_HOST=192.168.1.100
    export HIKVISION_USERNAME=admin
    export HIKVISION_PASSWORD='your-password'
    python scripts/smoke_test_live.py --channel 1
    python scripts/smoke_test_live.py --channel 8 --toggle --yes

The optional toggle test turns the upload-center link on and off and then
restores the original state.  It is intentionally guarded by ``--yes``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from custom_components.hikvision_away.api import (
    HikvisionAuthError,
    HikvisionError,
    HikvisionIsapiClient,
)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"Environment variable {name} is required.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel", type=int, default=int(os.environ.get("HIKVISION_CHANNEL", "1"))
    )
    parser.add_argument("--toggle", action="store_true", help="toggle on and off")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the live write test (required together with --toggle)",
    )
    parser.add_argument(
        "--timeout", type=int, default=10, help="HTTP timeout in seconds"
    )
    args = parser.parse_args()

    if args.toggle and not args.yes:
        parser.error("--toggle writes to the NVR; add --yes to confirm")

    host = _env("HIKVISION_HOST")
    username = _env("HIKVISION_USERNAME", "admin")
    password = _env("HIKVISION_PASSWORD")
    port = int(os.environ.get("HIKVISION_PORT", "80"))
    use_ssl = os.environ.get("HIKVISION_SSL", "0") in ("1", "true", "yes")
    verify_ssl = os.environ.get("HIKVISION_VERIFY_SSL", "0") in ("1", "true", "yes")

    client = HikvisionIsapiClient(
        host,
        username,
        password,
        port=port,
        use_ssl=use_ssl,
        verify_ssl=verify_ssl,
        timeout=args.timeout,
    )
    try:
        device = client.get_device_info()
        print(f"Device: {device.name} / {device.model}")
        print(f"Serial: {device.serial_number}  Firmware: {device.firmware_version}")

        channels = client.get_channels()
        print("Channels:")
        for channel in channels:
            print(f"  {channel.id}: {channel.name}")

        state = client.get_linkage_state(args.channel)
        print(
            f"Channel {args.channel}: upload-center="
            f"{'ON' if state.upload_center else 'OFF'}, "
            f"methods={list(state.notification_methods)}"
        )

        if not args.toggle:
            return 0

        original = state.upload_center
        print(f"\nWrite test: original upload-center is {original}")
        try:
            changed = client.set_upload_center(args.channel, True)
            after_on = client.get_linkage_state(args.channel).upload_center
            print(f"  -> ON requested, changed={changed}, readback={after_on}")
            if not after_on:
                raise SystemExit("NVR did not report upload-center=ON; aborting.")

            changed = client.set_upload_center(args.channel, False)
            after_off = client.get_linkage_state(args.channel).upload_center
            print(f"  -> OFF requested, changed={changed}, readback={after_off}")
            if after_off:
                raise SystemExit("NVR did not report upload-center=OFF; aborting.")
        finally:
            client.set_upload_center(args.channel, original)
            restored = client.get_linkage_state(args.channel).upload_center
            print(f"  -> restored original state: changed={restored == original}")
        return 0
    except HikvisionAuthError as err:
        print(f"Authentication failed: {err}", file=sys.stderr)
        return 2
    except HikvisionError as err:
        print(f"NVR error: {err}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
