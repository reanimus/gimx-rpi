# External controller AUTH2 table

`auth2.bin` is supplied by the user. **It is not redistributable.**
It is not covered by this project's source-code license and must not be
committed, embedded in source or binaries, or included in flashable images.

Validated extraction:

- Size: **512 bytes** (16 entries of 32 bytes).
- SHA-256: `98b700c568bd40d1a3a280a63918c357b1713666d7fb992ab08e19f96ba31b7d`
- Reference: [dogtopus's AUTH2 checker, revision fe1639180f9af329db5fdbe6a695b38a27ff8607](https://gist.github.com/dogtopus/931ba2cc911a1c13b21d1b3fd34de493/fe1639180f9af329db5fdbe6a695b38a27ff8607).
  The extraction matches the checker's published hash.
- Validation: reproduces the independently published response in
  [itewqq's weak-auth research](https://github.com/itewqq/ps4-controller-weak-auth).

## Install separately on the Pi 5

After copying your own file to the Pi's home directory:

```sh
sha256sum ~/auth2.bin
sudo install -d -m 0700 /var/lib/gimx/auth2
sudo install -m 0600 ~/auth2.bin /var/lib/gimx/auth2/auth2.bin
```

After the [Pi 5 image's first-boot installation](../README.md#flash-and-boot)
and pairing setup, launch with an explicit table path:

```sh
sudo gimx-ps4 run --usb-auth /dev/hidraw0 \
  --auth2-table /var/lib/gimx/auth2/auth2.bin --usb-auth-trace
```

The launcher and C backend independently require exactly 512 bytes and the
documented SHA-256. A missing or invalid file aborts startup. The option requires
USB or software mode and the current installer marker; the normal Bluetooth-donor
path is unchanged. No table is compiled into GIMX. See
[all three authentication modes](authentication-modes.md).

## Experimental response behavior

SET `03` validates the 40-byte Bluetooth packet, its CRC, and slot 0–15. It
computes SHA-1 over the chosen 32-byte entry, the fresh 32-byte challenge and a
20-byte salt. GET `04` returns a 48-byte Bluetooth packet containing the echoed
sequence/slot, digest, salt and CRC. Repeated GETs return the same response until
the next SET. Invalid SETs discard the previous response. These requests never
reach the USB controller; F0/F2/F1 continue through the USB donor. The salt is
random once per GIMX session and remains fixed within that session.
