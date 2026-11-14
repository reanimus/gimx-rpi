"""Query the local GIMX UDP connection-status extension (version 1)."""
import argparse
from dataclasses import asdict, dataclass
import json
import secrets
import socket
import struct
import time
from typing import Optional

INITIALIZED = 1 << 0
PS4_CONTROL = 1 << 1
PS4_INTERRUPT = 1 << 2
DS4_CONTROL = 1 << 3
DS4_INTERRUPT = 1 << 4
PS4_CONTROL_PENDING = 1 << 5
PS4_INTERRUPT_PENDING = 1 << 6
SHUTDOWN = 1 << 7
REPORT_SENT = 1 << 8
USB_AUTH = 1 << 9
USB_CONNECTED = 1 << 10
USB_UNSUPPORTED = 1 << 11
SOFTWARE_AUTH = 1 << 12
SOFTWARE_READY = 1 << 13


class StatusUnavailable(TimeoutError):
    """No matching reply: stopped/stalled GIMX, old build, wrong port, or lost packet."""


class StatusProtocolError(ValueError):
    """A matching reply uses an unsupported version or result code."""


@dataclass(frozen=True)
class ConnectionStatus:
    supported: bool
    controller_type: int
    initialized: bool
    ps4_control_connected: bool
    ps4_interrupt_connected: bool
    ds4_control_connected: bool
    ds4_interrupt_connected: bool
    ps4_control_pending: bool
    ps4_interrupt_pending: bool
    shutting_down: bool
    last_report_age_ms: Optional[int]
    usb_auth_mode: bool = False
    usb_auth_connected: bool = False
    usb_auth_unsupported: bool = False
    software_auth_mode: bool = False
    software_auth_ready: bool = False

    @property
    def ps4_connected(self):
        return (self.supported and self.initialized and not self.shutting_down
                and self.ps4_control_connected and self.ps4_interrupt_connected
                and not self.ps4_control_pending and not self.ps4_interrupt_pending)

    @property
    def ds4_connected(self):
        return (self.supported and self.initialized and not self.shutting_down
                and not self.software_auth_mode
                and (self.usb_auth_connected if self.usb_auth_mode else
                     self.ds4_control_connected and self.ds4_interrupt_connected))

    @property
    def auth_transport(self):
        return 'software' if self.software_auth_mode else ('usb' if self.usb_auth_mode else 'bluetooth')

    @property
    def auth_ready(self):
        if self.software_auth_mode:
            return self.supported and self.initialized and not self.shutting_down and self.software_auth_ready
        return self.ds4_connected

    @property
    def ready(self):
        """PS4 HID links and authentication backend available; not proof of console authentication."""
        return self.ps4_connected and self.auth_ready

    def to_dict(self):
        return {**asdict(self), 'ps4_connected': self.ps4_connected,
                'ds4_connected': self.ds4_connected, 'ready': self.ready,
                'auth_transport': self.auth_transport, 'auth_ready': self.auth_ready,
                'auth_unsupported': self.usb_auth_unsupported}


def decode_reply(data, nonce):
    """Return a snapshot, or None for an unrelated/malformed datagram."""
    if len(data) != 16:
        return None
    packet_type, version, result, controller, reply_nonce, flags, age = struct.unpack('!BBBBIII', data)
    if packet_type != 2 or reply_nonce != nonce:
        return None
    if version != 1 or result not in (0, 1):
        raise StatusProtocolError(f'Unsupported status response: version={version}, result={result}')
    supported = result == 0
    if not supported:
        flags, age = 0, 0xFFFFFFFF
    return ConnectionStatus(
        supported, controller,
        bool(flags & INITIALIZED), bool(flags & PS4_CONTROL), bool(flags & PS4_INTERRUPT),
        bool(flags & DS4_CONTROL), bool(flags & DS4_INTERRUPT),
        bool(flags & PS4_CONTROL_PENDING), bool(flags & PS4_INTERRUPT_PENDING),
        bool(flags & SHUTDOWN),
        age if flags & REPORT_SENT and age != 0xFFFFFFFF else None,
        bool(flags & USB_AUTH), bool(flags & USB_CONNECTED), bool(flags & USB_UNSUPPORTED),
        bool(flags & SOFTWARE_AUTH), bool(flags & SOFTWARE_READY))


def query_status(host='127.0.0.1', port=51914, timeout=0.5):
    """One request over the existing input UDP socket. Raises StatusUnavailable on silence."""
    if timeout <= 0 or not 1 <= port <= 65535:
        raise ValueError('Use a positive timeout and a port in 1..65535')
    nonce = secrets.randbits(32)
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, port))  # Restricts replies to the selected endpoint.
            sock.send(struct.pack('!BBI', 2, 1, nonce))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StatusUnavailable('No matching GIMX status reply')
                sock.settimeout(remaining)
                status = decode_reply(sock.recv(65535), nonce)
                if status is not None:
                    return status
    except OSError as exc:
        raise StatusUnavailable(f'GIMX status unavailable at {host}:{port}: {exc}') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=51914)
    parser.add_argument('--timeout', type=float, default=0.5)
    args = parser.parse_args()
    try:
        status = query_status(args.host, args.port, args.timeout)
    except (StatusUnavailable, StatusProtocolError, ValueError) as exc:
        print(json.dumps({'available': False, 'error': str(exc)}))
        return 2
    print(json.dumps({'available': True, **status.to_dict()}, indent=2))
    return 0 if status.ready else 1


if __name__ == '__main__':
    raise SystemExit(main())
