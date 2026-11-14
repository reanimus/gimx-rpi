# DS4 authentication modes

All three modes keep the Pi 5's Bluetooth connection to the PS4 and the existing
pairing configuration. Keyboard/UDP input remains the same.

| `--auth-mode` | Physical controller | External authentication files |
| --- | --- | --- |
| `bluetooth` (default) | DS4 connected to the Pi by Bluetooth | None |
| `usb` | DS4 plugged into the Pi by USB | AUTH2 table |
| `software` | None | AUTH2 table plus `key.pem`, `serial.txt`, `sig.bin` |

## Set up the Pi 5 image

Follow the [image generation and flashing instructions](../README.md#generate-the-sd-image).
The image includes all three modes and the UDP status client. On first boot,
the installer downloads dependencies, builds with OpenSSL 3 (`libssl-dev`), and
applies all four GIMX patches in order. Ethernet with internet access is required.

```sh
sudo cloud-init status --wait --long
sudo test -f /var/lib/gimx/auth-modes.ready && echo AUTH_MODES_READY
```

Complete the [PS4 pairing setup](../README.md#ps4-pairing-and-emulation), then
select a mode below. The Pi 4 companion only handles the pairing exchange.

Bluetooth donor mode needs no private authentication files. For USB mode,
install your own AUTH2 table; for software mode, also install your own keys:

```sh
# USB and software modes:
sudo install -d -m 0700 /var/lib/gimx/auth2
sudo install -m 0600 ~/auth2.bin /var/lib/gimx/auth2/

# Software mode only:
sudo install -d -m 0700 /var/lib/gimx/keys
sudo install -m 0600 ~/key.pem ~/serial.txt ~/sig.bin /var/lib/gimx/keys/
```

Choose one command:

```sh
# Bluetooth donor: unplug USB and press the physical DS4's PS button.
sudo gimx-ps4 run --auth-mode bluetooth

# USB donor: keep DS4 plugged in; no physical PS-button press required.
sudo gimx-ps4 run --auth-mode usb --usb-device /dev/hidraw0 \
  --auth2-table /var/lib/gimx/auth2/auth2.bin

# Software: turn on the PS4, with no DS4 connected to the Pi.
sudo gimx-ps4 run --auth-mode software \
  --auth2-table /var/lib/gimx/auth2/auth2.bin \
  --auth-keys /var/lib/gimx/keys
```

Add `--network-only` for UDP input without the keyboard mapping. Otherwise Home
sends the emulated PS button. Omit `--usb-device` to discover one USB DS4.
The earlier `--usb-auth [DEVICE]` syntax remains an alias for USB mode, and
`--usb-auth-trace` aliases `--auth-trace`. Both local modes require the table;
missing files never silently select a different mode.

## Private material and provenance

The three files were supplied by the user. They are **not redistributable**,
are not part of the source-code license, and must not be committed, embedded
in executables, or included in flashable images. Each user supplies their own
material; the hashes below identify the tested copy, not a runtime whitelist.
The original files remain in Downloads. The [table provenance and hash](auth2-table.md)
are documented separately. Do not distribute a provisioned filesystem containing
these files, including the private QEMU test disk.

Accepted formats:

- `key.pem`: unencrypted RSA-2048 private key, PKCS#1 or PKCS#8 PEM, at most
  16 KiB. OpenSSL checks the private key and keypair at startup. No password prompt.
- `serial.txt`: 16 or 32 hex characters, with optional surrounding whitespace.
  Sixteen characters decode to 8 bytes and are left-padded with 8 zero bytes,
  matching GP2040-CE's importer. The wire field is always 16 bytes.
- `sig.bin`: exactly 256 binary bytes, passed through as the controller certificate.
  It must belong with the supplied serial/key; this code cannot establish its
  Sony signature without a separately trusted issuer key.

The image builder embeds an explicit list of source patches and runtime tools;
it does not read private authentication files from the build host.
Tests generate throwaway credentials; a separate opt-in test exercises the user's
files without logging their contents. Logs contain request metadata only.

## Protocol and implementation

USB and software modes share the worker and Bluetooth AUTH2 implementation.
Software mode opens no hidraw device and registers no physical donor sockets.
The worker assembles five F0 nonce pages, signs the 256-byte nonce with
RSA-PSS/SHA-256 (MGF1-SHA-256, 32 random salt bytes), answers F2 readiness, then
returns nineteen F1 pages. The 1064-byte response contains the signature, padded
serial, big-endian modulus and exponent, certificate and final padding. New or
invalid challenges discard stale signing state; undersized reads do not advance
the page cursor. A retry of the most recent identical F0 page is idempotent.

Software mode supplies public calibration and firmware feature-report constants
for the initial 02/05/06/A3 requests. They do not describe a live donor and are
separate from the private identity files. Unknown reports remain unsupported.
RSA signing runs outside the main input loop. AUTH2 is unchanged from the working
USB arrangement, including its stable per-session salt.

References: [GP2040-CE signer](https://github.com/OpenStickCommunity/GP2040-CE/blob/dd80f3f325d3f5de4875e94e493c3fbd51ef6ee6/src/drivers/ps4/PS4Auth.cpp),
[report handling and public feature data](https://github.com/OpenStickCommunity/GP2040-CE/blob/dd80f3f325d3f5de4875e94e493c3fbd51ef6ee6/src/drivers/ps4/PS4Driver.cpp),
[serial importer](https://github.com/OpenStickCommunity/GP2040-CE/blob/dd80f3f325d3f5de4875e94e493c3fbd51ef6ee6/www/src/Pages/SettingsPage.jsx).

UDP status now reports `auth_transport: "software"` and `software_auth_ready`.
`ds4_connected` stays false in that mode; `auth_ready` reports availability of
the selected backend. `ready` combines backend availability with connected PS4
HID channels; it is still not an acknowledgment of console authentication.
