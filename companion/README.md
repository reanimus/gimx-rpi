# Pi 4 USB pairing companion for GIMX

Experimental replacement for the **EMUPS4PAIRING pairing microcontroller**, using
Ubuntu Server 26.04.1 LTS on a Raspberry Pi 4 Model B. Its USB-C port enumerates
as the firmware's DS4-style USB HID device. The host uses the existing
`ds4tool -t` commands. The Pi 4's own Bluetooth radio is not used.

The image contains a precompiled, statically linked ARM64 daemon and matching
source. First boot installs the service through cloud-init; it does not download
packages or compile code. Subsequent boots start the gadget automatically without
waiting for a network connection. Allow normal Linux boot time after moving cables.

**Verified in QEMU/HVF:** compilation with GCC 15, USB enumeration through
`dummy_hcd`, standard/feature descriptors, unmodified GIMX `ds4tool`, simulated
PS4 credential writes, malformed requests, state persistence, USB bus reset,
and corrupt-state rejection. The user later reported successful pairing through
the physical Pi 4 USB-C port and their PS4. Linux describes Raw Gadget as an
experimental/debugging interface; that is appropriate to this prototype.

During physical testing, the user observed a [kernel Oops in Raw Gadget teardown](../docs/pi4-gadget-kernel-crash.md)
on Ubuntu `7.0.0-1017-raspi`. The resulting stuck task held the pairing-state
lock until reboot. This incident is separate from the successful PS4 pairing and
three-mode GIMX tests; the cause inside the kernel has not been isolated.

## Flash and initialize

1. Flash `dist/companion/ubuntu-26.04.1-gimx-pairing-pi4.img` onto a 16 GB or larger
   microSD card. In Raspberry Pi Imager use **Use custom** and skip OS customization.
2. Boot a Pi 4 Model B. Ethernet is useful for first-boot observation and SSH but
   the companion service has no Internet dependency. Connect the USB-C port to
   a host using a data-capable cable; the USB-A ports cannot act as the gadget port.
3. For SSH, log in as `gimx` at the Pi 4's Ethernet IP with the password in the
   adjacent `.credentials.txt` file. Change it with `passwd`.
4. Verify initialization:

   ```sh
   sudo cloud-init status --wait --long
   sudo systemctl status gimx-pairing --no-pager
   sudo journalctl -u gimx-pairing --no-pager -n 30
   sudo pairing-status
   ```

If the service fails, inspect the journal. The launcher requires a Pi 4 Model B
and exactly one USB device controller. The stock image's `dwc2` overlay is changed
to `dtoverlay=dwc2,dr_mode=peripheral`; `raw_gadget` is loaded by the service.
The pinned Pi kernel (7.0.0-1017-raspi) was inspected and contains both components.
No replacement kernel or out-of-tree module is installed on the Pi image.

Provide sufficient, stable power for the Pi 4. A PS4 USB port alone is not a
verified Pi 4 power supply. If using a separate power source, use a power/data
arrangement designed to prevent backfeeding either host; do not connect two
unmanaged 5 V sources together. Brownouts and abrupt SD power loss can interrupt
boot or damage the filesystem even though pairing-state updates are atomic.

## Use it exactly like the pairing board

First identify the Pi 5 Bluetooth adapter address that will be used by GIMX.
If testing a changed address, apply and verify it on the Pi 5 before pairing.
That address must remain the same when GIMX later connects to the PS4.

1. Connect **Pi 4 USB-C → Pi 5 USB-A**. Wait for the Pi 4 to boot and enumerate.
   On the Pi 5:

   ```sh
   sudo ds4tool -t
   sudo ds4tool -t -s PI5_BLUETOOTH_ADDRESS
   sudo ds4tool -t -m 00:00:00:00:00:00
   sudo ds4tool -t
   ```

   Replace `PI5_BLUETOOTH_ADDRESS` with the actual address. The final read must
   print that address, a zero master address, and a zero key. Do not treat a zero
   exit code with no device output as success. The final read also ensures the
   preceding state update has been persisted before you disconnect.
2. Move the Pi 4's USB-C data connection to a **PS4 USB-A port**. If it lost power,
   wait for it to boot again. Then allow time for USB enumeration and pairing.
   With Ethernet available, `sudo pairing-status` reports when a nonzero PS4
   address/key has been saved, without printing the key.
3. Move the Pi 4 back to the Pi 5. Wait for boot/enumeration and run:

   ```sh
   sudo ds4tool -t
   ```

   This should print the Pi 5 adapter address, the PS4 Bluetooth master address,
   and the captured 16-byte link key. A zero key means the console pairing has
   not completed. Keep the output private.
4. Use the PS4 address/key in `sudo gimx-ps4 configure` on the Pi 5, alongside the
   real DS4's address/key. See the [main PS4 setup guide](../README.md).

This companion performs the USB pairing exchange and stores its result. Ongoing
authentication runs on the Pi 5 using one of the [three supported modes](../docs/authentication-modes.md):
a Bluetooth donor, a USB donor plus AUTH2 table, or software authentication with
the table and private key files.

## State and debugging

State is stored in `/var/lib/gimx-pairing/state.bin` (root-only), not in the boot
partition. A separate lock prevents two daemons from sharing it. Each update uses
write → fsync → atomic rename → directory fsync. A malformed state file causes
startup to fail rather than silently discarding credentials. The service logs
whether a key is present but never logs the key itself. `ds4tool -t` intentionally
returns the key, as the original firmware does.

HID feature reports implemented from upstream:

| Request | Behavior |
| --- | --- |
| GET_REPORT `0x03a3` / `0x0302` | Firmware and calibration data |
| GET_REPORT `0x0312` | Emulated adapter and console Bluetooth addresses |
| SET_REPORT `0x0312`, 6 bytes | Set emulated adapter address, little-endian |
| SET_REPORT `0x0313`, 23 bytes | Report ID, console address, 16-byte link key |
| GET_REPORT `0x0313`, 16 bytes | Raw key without report-ID prefix (GIMX extension) |
| SET_REPORT `0x0314` | Acknowledge, no state change, matching upstream intent |

Unsupported control requests stall; oversized IN requests receive valid short
responses. The latter matters because the pinned ARM64 GIMX USB probing code
requests a report-descriptor length larger than the descriptor's actual size.
OUT lengths are bounded and checked before copying state. Interrupt IN sends
neutral controller reports and interrupt OUT is drained. Endpoint addresses are
selected from the UDC's advertised interrupt capabilities.

Optional `PAIRING_TRACE=1` logs setup-request metadata (not data payloads).
Avoid USB network/serial composite functions on the PS4 connection; the supplied
gadget deliberately exposes one HID interface. Use physical Ethernet for management.

## Rebuild

On an ARM64 Ubuntu 26.04 system with build-essential:

```sh
cd companion
python3 src/generate-descriptors.py
cc -O2 -Wall -Wextra -Werror -std=gnu11 -march=armv8-a -mtune=generic \
  -static -pthread -o pairing-gadget src/pairing.c
```

Copy that binary to `dist/companion/pairing-gadget` on the image-building host.
Then, from the project root:

```sh
.venv/bin/python scripts/build-companion-image.py \
  --base /tmp/gimx-ubuntu-base.img.xz \
  --binary dist/companion/pairing-gadget \
  --output dist/companion/my-pairing-pi4.img
```

The base is the same checksum-pinned official image used by the Pi 5 builder.
Optional `--ssh-key path/to/public-key` enables SSH key login and disables SSH
password login. The builder rejects non-ARM64 or dynamically linked binaries,
checks the base checksum, verifies written FAT content, and emits a final checksum,
manifest, and unique credentials. It refuses to overwrite an existing image.
Matching GPL source/notices are included under `/usr/local/src/gimx-pairing`.

## QEMU protocol test reproduction

The ARM64 Ubuntu VM needs `raw_gadget`, `dummy_hcd`, libusb, and the existing GIMX
`ds4tool`. Our VM kernel omitted `dummy_hcd`; `tests/dummy_hcd.c` is the pinned
Linux v7.0 implementation built against its matching installed Ubuntu headers:

```sh
make -C tests
sudo modprobe raw_gadget
sudo insmod tests/dummy_hcd.ko
sudo systemd-run --unit=pairing-debug \
  "$PWD/pairing-gadget" dummy_udc dummy_udc.0 /var/lib/gimx-pairing-test
sudo python3 tests/test_usb.py
```

The test refuses to address a real controller and uses synthetic keys only.
The simulated kernel module is test-only and is **not shipped in the Pi image**.
Full test results are in `dist/companion/qemu-verification.log`.

Protocol reference: [EMUPS4PAIRING source](https://github.com/matlo/GIMX-firmwares/tree/ab2215ebad5d50fd25b6b40ac74254f1f4c0bda5/EMUPS4PAIRING).
See `NOTICE`, `COPYING`, and retained source notices for licensing.
