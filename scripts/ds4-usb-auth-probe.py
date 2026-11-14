#!/usr/bin/env python3
"""Probe a USB DS4; --challenge tests F0/F2/F1 signing, not PS4 acceptance.

Linux only. Run with GIMX stopped and the real DS4 connected directly by USB.
No Bluetooth pairing reports, USB resets, or kernel-driver detaches are sent.
The challenge option changes the controller's transient authentication session.
Protocol references and limitations: docs/usb-auth-investigation.md.
"""
import argparse
import base64
import binascii
import fcntl
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import time


class ProbeError(RuntimeError):
    pass


def ioctl_number(direction, number, size):
    # Linux asm-generic ioctl ABI, including Raspberry Pi ARM64.
    return (direction << 30) | (size << 16) | (ord('H') << 8) | number


def discover(sysroot=Path('/sys/class/hidraw')):
    found = []
    for device in sorted(sysroot.glob('hidraw*')):
        info = (device / 'device/uevent').read_text()
        if re.search(r'^HID_ID=0003:0*054[Cc]:0*(05[Cc]4|09[Cc][Cc])$', info, re.M):
            found.append('/dev/' + device.name)
    if len(found) != 1:
        raise ProbeError(f'Expected one USB DS4, found {len(found)}. '
                         'Disconnect the Pi 4 pairing gadget and other controllers; '
                         'or select the real DS4 with --device /dev/hidrawN.')
    return found[0]


class Hidraw:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            info = bytearray(8)
            fcntl.ioctl(self.fd, ioctl_number(2, 0x03, 8), info, True)
            bus, vid, pid = struct.unpack('=IHH', info)
            if bus != 3 or vid != 0x054c or pid not in (0x05c4, 0x09cc):
                raise ProbeError('Selected hidraw device is not a USB Sony DS4.')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.product_id = pid
        except BaseException:
            os.close(self.fd)
            raise

    def get(self, report_id, size):
        data = bytearray(size)
        data[0] = report_id
        length = fcntl.ioctl(self.fd, ioctl_number(3, 0x07, size), data, True)
        if length != size or data[0] != report_id:
            raise ProbeError(f'GET {report_id:02X}: unexpected length/ID '
                             f'({length} bytes, ID {data[0]:02X})')
        return bytes(data)

    def set(self, data):
        buf = bytearray(data)
        length = fcntl.ioctl(self.fd, ioctl_number(3, 0x06, len(buf)), buf, True)
        if length != len(buf):
            raise ProbeError(f'SET {buf[0]:02X}: short write ({length} bytes)')

    def close(self):
        os.close(self.fd)


def with_crc(data):
    return bytes(data) + struct.pack('<I', binascii.crc32(data) & 0xffffffff)


def check_crc(data):
    if len(data) < 5 or with_crc(data[:-4]) != data:
        raise ProbeError(f'Report {data[0]:02X}: USB CRC mismatch')


def challenge_pages(nonce, sequence):
    if len(nonce) != 256 or not 1 <= sequence <= 255:
        raise ValueError('Expected a 256-byte nonce and nonzero 8-bit sequence')
    return [with_crc(bytes((0xf0, sequence, page, 0)) +
                     nonce[page * 56:(page + 1) * 56].ljust(56, b'\0'))
            for page in range(5)]


def collect_response(device, nonce, sequence, timeout=30.0, clock=time.monotonic, sleep=time.sleep):
    for page in challenge_pages(nonce, sequence):
        device.set(page)
        sleep(0.02)
    deadline = clock() + timeout
    while clock() < deadline:
        status = device.get(0xf2, 16)
        check_crc(status)
        if status[1] != sequence:
            raise ProbeError('F2 sequence does not match this challenge')
        if status[2] == 0:
            break
        if status[2] != 0x10:
            raise ProbeError(f'DS4 signing status: 0x{status[2]:02x}')
        sleep(0.1)
    else:
        raise ProbeError('Timed out waiting for DS4 signing response')
    parts = []
    for page in range(19):
        data = device.get(0xf1, 64)
        check_crc(data)
        if data[:4] != bytes((0xf1, sequence, page, 0)):
            raise ProbeError(f'F1 sequence/page mismatch at page {page}')
        parts.append(data[4:60])
    return b''.join(parts)


def der(tag, content):
    size = len(content)
    if size < 128:
        length = bytes((size,))
    else:
        encoded = size.to_bytes((size.bit_length() + 7) // 8, 'big')
        length = bytes((0x80 | len(encoded),)) + encoded
    return bytes((tag,)) + length + content


def public_key_pem(modulus, exponent):
    def integer(value):
        data = value.to_bytes((value.bit_length() + 7) // 8, 'big')
        if data[0] & 0x80:
            data = b'\0' + data
        return der(2, data)
    encoded = base64.b64encode(der(0x30, integer(modulus) + integer(exponent))).decode()
    lines = '\n'.join(encoded[i:i + 64] for i in range(0, len(encoded), 64))
    return '-----BEGIN RSA PUBLIC KEY-----\n' + lines + '\n-----END RSA PUBLIC KEY-----\n'


def verify_response(nonce, response, openssl='openssl'):
    if len(nonce) != 256 or len(response) != 1064:
        raise ProbeError('Unexpected nonce or assembled response length')
    # F1 payload: signature[256], serial[16], N[256], E[256], certificate[256], pad[24].
    modulus = int.from_bytes(response[272:528], 'big')
    exponent = int.from_bytes(response[528:784], 'big')
    if modulus.bit_length() != 2048 or exponent < 3 or exponent > 0xffffffff or not exponent & 1:
        raise ProbeError('Response does not contain an expected RSA-2048 public key')
    with tempfile.TemporaryDirectory(prefix='ds4-auth-') as directory:
        directory = Path(directory)
        (directory / 'public.pem').write_text(public_key_pem(modulus, exponent))
        (directory / 'signature').write_bytes(response[:256])
        (directory / 'nonce').write_bytes(nonce)
        result = subprocess.run([
            openssl, 'dgst', '-sha256', '-verify', str(directory / 'public.pem'),
            '-signature', str(directory / 'signature'),
            '-sigopt', 'rsa_padding_mode:pss', '-sigopt', 'rsa_pss_saltlen:auto',
            str(directory / 'nonce')], capture_output=True, timeout=10)
        if result.returncode:
            raise ProbeError('RSA-PSS signature verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', help='Real DS4 hidraw device; auto-select if omitted')
    parser.add_argument('--challenge', action='store_true',
                        help='Send a fresh nonce and verify the USB signing response')
    parser.add_argument('--timeout', type=float, default=30.0, help='Signing wait in seconds (1..120)')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error('--timeout must be in 1..120')
    if args.challenge and not shutil.which('openssl'):
        parser.error('OpenSSL is needed for signature verification')
    path = args.device or discover()
    device = Hidraw(path)
    try:
        print(f'USB DS4: {path}, 054c:{device.product_id:04x}', flush=True)
        device.get(0xa3, 49)
        print('Firmware feature report A3: readable', flush=True)
        status = device.get(0xf2, 16)
        check_crc(status)
        print(f'Authentication feature F2: readable, valid CRC, state=0x{status[2]:02x}', flush=True)
        if not args.challenge:
            print('Read-only probe complete. This does not test challenge signing.')
            return
        print('Sending a fresh USB challenge (F0); waiting for F2/F1...', flush=True)
        nonce = secrets.token_bytes(256)
        response = collect_response(device, nonce, secrets.randbelow(255) + 1, args.timeout)
        verify_response(nonce, response)
        print('PASS: fresh USB challenge signed; all 19 response pages and RSA-PSS signature verified.')
        print('This verifies signing with the returned public key, not its Sony certificate,')
        print('PS4 acceptance, or the additional Bluetooth 03/04 exchange.')
    finally:
        device.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ProbeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'FAIL: {exc}')
