# GIMX UDP connection status

The Pi 5 image includes a project patch to GIMX commit
`e44a1cc9823939165d3d92418e9d679c66496ee1`. It exposes DS4 Bluetooth HID
channel state through the existing UDP input socket. Existing controller queries
and input packets remain compatible.

After configuring pairing, start GIMX:

```sh
sudo gimx-ps4 run --network-only
```

Omit `--network-only` to retain the keyboard mapping alongside UDP input.
The helper binds the input/status socket to `127.0.0.1:51914` by default;
`--udp-port PORT` changes it. In another terminal:

```sh
gimx-status
```

The JSON output includes `ps4_connected`, `ds4_connected`, `ready`, individual
HID channel states, pending connection flags, and `last_report_age_ms`.
Exit codes: **0** both links ready; **1** a reply reports not ready or an unsupported
transport; **2** status unavailable or a protocol error.

The installer also makes this dependency-free Python module available to the
system Python interpreter:

```python
from gimx_status import query_status, StatusUnavailable

try:
    status = query_status(timeout=0.5)
    print("PS4:", status.ps4_connected)
    print("Real DS4:", status.ds4_connected)
    print("Both HID links ready:", status.ready)
except StatusUnavailable:
    print("GIMX status unavailable")
```

Polling at 5–10 Hz is sufficient for monitoring. Each call sends one request;
there are no subscriptions or automatic retries. An unavailable reply is a
separate state: GIMX might have exited, stalled, be an old build, or be listening
on another port. GIMX can exit on disconnect before answering another query.

`ps4_connected` means the PS4 control and interrupt L2CAP channels are connected
and no longer pending. `ready` additionally requires both real DS4 HID channels,
an available USB donor, or an initialized software signer, according to the
selected authentication mode. `auth_ready` reports backend availability.
`ds4_connected` remains false in software mode because no physical donor exists.
These are local transport observations, not proof that the PS4 authenticated the
controller or accepted an input. Report age measures time since a complete
successful local report write, not a console acknowledgment; it can remain
available after disconnection. No report yet is represented as `null`.

## Installation in the Pi 5 image

The [Pi 5 SD image](../README.md#generate-the-sd-image) includes the status patch,
Python client, CLI and launcher. Its online first-boot installer builds GIMX and
installs these tools. Once installation and PS4 pairing are complete, start GIMX
and query it using the commands above. Status is available with all three
authentication modes.

## Wire protocol, version 1

Send to the existing GIMX `--src` socket. Multi-byte integers are unsigned,
network byte order. Requests must be exactly 6 bytes; responses are 16 bytes.

| Message | Python struct format | Fields |
| --- | --- | --- |
| Request | `!BBI` | packet type `2`, version `1`, random nonce |
| Reply | `!BBBBIII` | type `2`, version `1`, result, controller type, echoed nonce, flags, report age in ms |

Result: `0` DS4 Bluetooth snapshot; `1` unsupported transport; `2` unsupported
request version. Invalid request lengths are silently ignored. Unsupported
requests/transports return zero flags and age `0xffffffff`. Clients should
match endpoint, type, length, version, and nonce before using a reply.

| Flag bit | Meaning |
| --- | --- |
| 0 | Bluetooth DS4 connector initialized |
| 1 | PS4 control channel connected |
| 2 | PS4 interrupt channel connected |
| 3 | Real DS4 control channel connected |
| 4 | Real DS4 interrupt channel connected |
| 5 | PS4 control channel connection pending |
| 6 | PS4 interrupt channel connection pending |
| 7 | Connector shutting down |
| 8 | At least one complete report write succeeded |
| 9 | Experimental USB donor mode selected |
| 10 | USB donor transport available |
| 11 | An unsupported Bluetooth request was seen in USB/software mode |
| 12 | Software authentication selected |
| 13 | Software authentication worker available |

Bits 9–13 extend v1 without changing its packet layout. In USB donor mode bits
3–4 remain clear, and the Python client uses bit 10 for `ds4_connected`/`ready`.
The additional JSON fields are `auth_transport`, `usb_auth_mode`,
`usb_auth_connected`, and `usb_auth_unsupported`. Software mode sets bits 12/13,
leaves USB mode/connected bits and donor HID bits clear, and adds JSON fields
`software_auth_mode` and `software_auth_ready`. `auth_transport` is `bluetooth`,
`usb`, or `software`; `auth_ready` and `ready` use the selected backend.
`auth_unsupported` aliases the historical `usb_auth_unsupported` field (bit 11).
Unsupported-request state persists for the session and is independent of transport
connectivity. Software readiness proves local initialization, not Sony certificate
validity or console acceptance. Older clients may show not-ready for software mode;
install the matching Python/status client with the update.

Age `0xffffffff` means no successful report write; elapsed time saturates at
`0xfffffffe`. Status queries do not trigger controller reports, including when
using GIMX's input-driven clock. This is a project extension, not an upstream
GIMX API. It has no authentication; the provided launcher binds to loopback.

## Verification

The patched CLI compiled in Ubuntu 26.04.1 ARM64 under QEMU/HVF. Tests exercise
the actual C dispatcher and Bluetooth connector with simulated sockets and a
controlled clock, under AddressSanitizer and UndefinedBehaviorSanitizer.
They cover pending/connected/disconnected states, shutdown, successful writes,
age saturation, nonce echo, malformed requests, unsupported transports and
versions, legacy input/query compatibility, and avoiding output on status queries.
Nine Python tests cover decoding, errors, USB donor flags, and real loopback UDP replies/timeouts.
An end-to-end test against the installed GIMX binary also verifies the unsupported
remote-transport status reply and legacy UDP query/input compatibility.

## Disconnect logs

The current build always writes timestamped connection events to stderr; no
USB authentication trace option is required. The existing launch commands show
them directly. To retain them, append `2>&1 | tee gimx-session.log` to your command.

Example (illustrative timestamp):

```text
[2026-11-14T18:00:00.123Z] GIMX connection: PS4 disconnected controller=0 channel=control reason=channel_closed errno=0
```

Events distinguish PS4 and Bluetooth DS4 channel closure, a pending PS4 connection
failure, USB/software authentication backend failure, and a local session stop.
Deliberate channel cleanup does not produce misleading disconnect events.
`controller` is the zero-based GIMX slot. Timestamps use UTC.

`channel_closed` does not identify why the Bluetooth link was lost: the callback
has no HCI reason. `errno=0` means no error code was supplied. Backend failures
include the operation and error code when available; a dead USB worker alone
cannot distinguish an unplugged donor from a worker failure. These events do
not prove console authentication acceptance and do not add automatic reconnect.
The UDP status protocol is unchanged; GIMX may exit before another status query.

Successful PS4 and Bluetooth DS4 connections emit `connected` once both HID
channels are established. Pending or partial connections do not produce it.
After a successful full write of an AUTH2 response or RSA response page,
GIMX emits `PS4 auth sent`, with `reason=auth2_reply` or `rsa_response_page`.
This is a per-response transmission event, not a claim that the whole exchange
has completed or that the console accepted it. Connecting alone does not emit
it. Dropped replies and failed/short writes do not emit it. Deliberately corrupted
AUTH2 replies use `reason=auth2_reply_fault_corrupted`.

Hardware fault tests showed inputs eventually stopped registering without an
explicit error or transport disconnect in either the corrupt or ignored-reply
case. Accordingly, status reports what we know: connected and auth sent. These
observations do not establish that every possible protocol rejection is silent.

## Authentication fault experiment

Append one of these flags to your existing `sudo gimx-ps4 run ...` command:

- `--auth-observe`: baseline; log received PS4 control metadata without mutation.
- `--auth-fault-corrupt`: flip one bit in each outbound AUTH2 SHA1 digest
  (Bluetooth feature report `04`), then recompute the CRC. Framing, sequence,
  slot and salt remain intact.
- `--auth-fault-ignore`: drop each outbound AUTH2 report `04` reply completely.
  The incoming challenge is still processed; its SET_REPORT handshake is not
  dropped. This tests missing responses, not a malformed response.

Fault modes are mutually exclusive and enable observation automatically. They
work with Bluetooth, USB and software donors. They do not change RSA F0/F1/F2
messages, pairing data, keys or the AUTH2 table. No flag means no fault injection;
the launcher clears inherited experiment environment settings on each run.
For direct `gimx` invocation use `sudo env GIMX_DS4_AUTH_FAULT=corrupt gimx ...`
or `ignore`, or `GIMX_DS4_AUTH_OBSERVE=1` for the baseline.

Run three fresh sessions using the same working mode and console state. Capture
stderr with `2>&1 | tee baseline.log` (then `corrupt.log` and `ignore.log`). Keep
each running until disconnection or at least as long as the baseline observation
window, and record when on-screen input stops working. Confirm an `AUTH fault`
line actually appears: enabling the flag alone does not prove injection occurred.
Logs include timestamped fault counts, incoming request header/report/length,
HID handshake status if received, and existing disconnect events. No report
payloads or keys are printed. A received HID handshake is logged as such, never
labelled PS4 authentication acceptance/rejection without further evidence.

A repeatable negative response can establish a rejection indicator. Its absence
only establishes that no such rejection was observed during that interval; it
does not prove acceptance. In particular, the console may silently ignore an
invalid reply, retry, or stop accepting input without closing the link. Compare
both fault modes and the baseline before defining a timeout-based heuristic.
These flags were tested with simulated transport and compiled in QEMU; actual
PS4 behavior must be measured on hardware.
