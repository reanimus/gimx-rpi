# Ubuntu 26.04.1 LTS / Raspberry Pi 5 / GIMX PS4 image

Generate a flashable SD image that uses the Pi 5's built-in Bluetooth controller
for GIMX DualShock 4 emulation. All three authentication modes have been verified
by the user on their Pi 5 / PS4 setup: Bluetooth donor, USB donor with an AUTH2
table, and software authentication with private key files and no controller.

This is an **online first-boot installation image**: Ubuntu, source patches and
setup tools are included; dependencies and the pinned GIMX source are downloaded
and compiled on first boot. Private authentication files are installed separately.
No Bluetooth address or pairing keys are changed automatically.

Project code and documentation are distributed under GNU GPLv3. See [LICENSE](LICENSE)
and [licensing details](LICENSING.md) for the retained upstream notices and the
Linux kernel test-file exception. Private controller authentication material is
not part of the repository or this license.

## Generate the SD image

From the project root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
curl -L --fail --silent --show-error \
  https://cdimage.ubuntu.com/releases/26.04/release/ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz \
  -o /tmp/gimx-ubuntu-base.img.xz
.venv/bin/python scripts/build-image.py \
  --base /tmp/gimx-ubuntu-base.img.xz \
  --output dist/my-gimx-pi5.img
```

The builder writes the flashable `.img`, its `.img.sha256`, a `.manifest.json`,
and private `.credentials.txt` login details beside it. It refuses to overwrite
an existing image; choose a new output filename for each build. The example
above produces `dist/my-gimx-pi5.img`; use that path and its accompanying credentials
when following the flashing instructions below.

Optional `--ssh-key ~/.ssh/id_ed25519.pub` installs your public key and disables
SSH password authentication. The builder checks the pinned Canonical SHA-256,
edits only the FAT boot partition, and reads back its cloud-init files. It needs
Python, OpenSSL with SHA-512 password support, and roughly 7 GB free disk space.
No mounted image, root privileges, or Linux host is required.

GIMX is pinned to `e44a1cc9823939165d3d92418e9d679c66496ee1` and its submodule
commits. Apt dependencies are resolved from Ubuntu's repositories at installation
time, so package versions can change. See the generated manifest and guest
`/var/log/gimx/build-manifest.txt`. Compile fixes are in `payload/gimx-install`.
The builder includes all four patches, all three authentication modes, and the
UDP status client. The [Pi 4 pairing image](companion/README.md#rebuild) has its own builder.

## Flash and boot

1. Flash `dist/ubuntu-26.04.1-gimx-pi5.img` using Raspberry Pi Imager's **Use custom**
   option or another raw-image writer. Use a 16 GB or larger microSD card.
   Do not apply Imager OS customizations: the image already contains cloud-init settings.
2. Connect Ethernet, a USB keyboard, display if desired, and suitable Pi 5 power.
3. Find its Ethernet IP in your router. Log in as `gimx`, using the generated
   password in `dist/ubuntu-26.04.1-gimx-pi5.credentials.txt`. SSH is enabled.
   The password is unique to this build. Change it with `passwd`.
4. Wait for installation and inspect the actual result:

   ```sh
   sudo cloud-init status --wait --long
   sudo tail -80 /var/log/gimx/install.log
   sudo test -f /var/lib/gimx/install.ready && echo BUILD_READY
   ```

   Cloud-init completion alone is not proof of successful installation. On failure,
   fix the reported issue and retry `sudo gimx-install`. The build runs with two jobs.
   The SD root partition expands through the stock Ubuntu image's first-boot setup.

## Test the built-in Bluetooth controller

```sh
sudo gimx-bt inspect
sudo gimx-bt set 12:34:56:78:9A:BC
sudo gimx-bt restore
```

The example address is only for an isolated initial test. Use the Pi adapter
address associated with your PS4 pairing for actual play. Do not give the Pi the
same address as the real DS4: both participate in the Bluetooth connection.
For a fresh GIMX PS4 pairing, you can use the Pi's original address; address
spoofing is not itself the PS4 authentication mechanism.

`--hci hci0` selects the controller explicitly. The tool refuses address writes
on a non-UART controller; it never silently substitutes a USB dongle.
`--method mgmt` tests only `btmgmt public-addr`;
`--method vendor` tests only Broadcom's `Write_BD_ADDR` (opcode `0xfc01`).
The default tries management first and falls back to the vendor command, gated
on Broadcom/Cypress manufacturer IDs 15 and 305. Vendor parameters are sent least-significant octet first.
A successful command exit code is insufficient: the HCI completion status,
immediate controller readback, post-reopen readback, and kernel address are checked.

If Linux retains an old address or firmware restores the original during reopen,
the command reports failure. Save that log; GIMX may otherwise bind the wrong
local address. Address writes may be volatile; inspect after a cold reboot.
The original address and device path are saved in `/var/lib/gimx/original-bdaddr.json`.
`restore` uses that record; it is not overwritten by later tests.

Logs are in `/var/log/gimx/bt-*.log`. For a full connection trace, run in a separate
SSH session before starting GIMX:

```sh
sudo btmon -i hci0 -w /var/log/gimx/ps4.btsnoop
```

Bluetooth captures can contain link keys; keep them private. A successful address
change is only the first test, not evidence of working PS4 emulation.

The installer masks `bluetooth.service` to keep bluetoothd from intercepting GIMX
connections. It leaves the kernel driver, Pi firmware, and UART attachment services
intact. To return to ordinary Bluetooth, stop GIMX and run:

```sh
sudo systemctl unmask --runtime bluetooth.service
sudo systemctl unmask bluetooth.service
sudo systemctl start bluetooth.service
```

## PS4 pairing and emulation

All three authentication modes use the same pairing configuration. The default
mode uses a real DualShock 4 connected wirelessly through the Pi; USB donor and
software modes are selected after pairing using the [mode setup guide](docs/authentication-modes.md).
The pairing exchange uses the [Pi 4 companion](companion/README.md) or an
ATmega32U4 device (Teensy 2.0 / Arduino Micro) flashed with `EMUPS4PAIRING`.
Only the CLI is installed; there is no desktop or GIMX GUI launcher.

Follow the upstream [PS4 guide](https://gimx.fr/wiki/index.php?title=PS4).
The CLI equivalents of the launcher's pairing sequence are below. Substitute
actual addresses; use a local private shell, since `ds4tool` prints pairing keys.
These steps re-pair the real controller; reconnect it to the PS4 over USB to
restore ordinary console pairing later.

1. Set/verify the intended Pi address, then plug the real DS4 into the Pi by USB:

   ```sh
   sudo ds4tool
   DS4_KEY=$(openssl rand -hex 16)
   sudo ds4tool -m PI_ADAPTER_ADDRESS -l "$DS4_KEY"
   ```

   Record the real DS4 address and generated key privately.
2. Plug the prepared Pi 4 companion or ATmega32U4 pairing device into the Pi 5:

   ```sh
   sudo ds4tool -t -s PI_ADAPTER_ADDRESS
   sudo ds4tool -t -m 00:00:00:00:00:00
   ```

3. Move that pairing device to the PS4, wait a few seconds, then move it back to
   the Pi and read the console's address and nonzero link key:

   ```sh
   sudo ds4tool -t
   ```

4. Store the two pairings interactively:

   ```sh
   sudo gimx-ps4 configure
   ```

   Enter the Pi address, real DS4 address and key, then PS4 address and key.
   These are stored in `/root/.gimx/bluetooth/<PI_ADDRESS>/linkkeys`, with the DS4
   entry first and PS4 entry second, as expected by the pinned GIMX code.
   Existing pairings are not overwritten. Back up/remove them explicitly to reconfigure.
5. For Bluetooth donor mode, unplug the DS4's USB cable, start GIMX, and press
   the physical DS4's PS button:

   ```sh
   sudo gimx-ps4 run
   ```

   Use a keyboard physically connected to the Pi: **Home = PS**, **arrows = D-pad**,
   **Space = Cross**, **Backspace = Circle**, **Enter = Options**.
   SSH keystrokes are not the emulated input device. Ctrl-C stops GIMX.
   A custom mapping can be placed under `/root/.gimx/config/` and selected with
   `sudo gimx-ps4 run --config filename.xml`.
   For USB donor or software mode, use the [mode-specific commands and file setup](docs/authentication-modes.md#set-up-the-pi-5-image).

Success requires PS4 input response, sustained authentication (test at least
15 minutes), and reconnects after restarting GIMX and rebooting the Pi. Record
kernel/BlueZ versions, address-test results, and connection logs separately.
A generic QEMU guest cannot validate the Pi's Bluetooth radio, firmware,
simultaneous DS4/PS4 links, latency, or compatibility with console firmware.

## Sources

- [Canonical Ubuntu 26.04 release images and checksums](https://cdimage.ubuntu.com/releases/26.04/release/)
- [GIMX Linux build instructions](https://gimx.fr/wiki/index.php?title=Development_on_Linux)
- [GIMX PS4 hardware/pairing requirements](https://gimx.fr/wiki/index.php?title=PS4)
- [Pinned GIMX source](https://github.com/matlo/GIMX/tree/e44a1cc9823939165d3d92418e9d679c66496ee1)
- [BlueZ management protocol: Set Public Address](https://github.com/bluez/bluez/blob/master/doc/mgmt-protocol.rst)
- [Infineon Broadcom address-write discussion](https://community.infineon.com/t5/Wi-Fi-Bluetooth-for-Linux/How-to-change-Bluetooth-MAC-address-on-Raspberry-Pi-4/td-p/179476)

## UDP connection status

The Pi 5 build now includes a UDP status extension and Python client. Run
`sudo gimx-ps4 run --network-only`, then `gimx-status` in another terminal.
See [the protocol and Python API](docs/udp-status.md).
Status distinguishes established PS4/DS4 HID links, pending connections, and
unavailable replies; it does not confirm console authentication or input acceptance.

## Pi 4 pairing companion

A companion Ubuntu image and USB gadget implementation are available in
[companion/README.md](companion/README.md). It targets the EMUPS4PAIRING role and
uses the same `ds4tool -t` cable-swap workflow. Protocol interoperability is tested
under QEMU; the user subsequently reported successful pairing with their PS4.
Note however that at the time of writing there is a [bug in the USB gadget driver](docs/pi4-gadget-kernel-crash.md)
that causes issues on disconnect/reconnect. You may need to manually reboot the
Pi between connections in order to work around it.

## DS4 authentication modes

GIMX supports a Bluetooth donor (default, no authentication files). It now also
supports a USB donor (with the external AUTH2 table), or software authentication
without a controller (table plus user-provided `key.pem`, `serial.txt` and `sig.bin`).

See [mode selection, installation and private-file provenance](docs/authentication-modes.md).
All private material is **non-redistributable** and excluded from source
and images. The Pi 5 image includes the code only; files are installed
separately.
