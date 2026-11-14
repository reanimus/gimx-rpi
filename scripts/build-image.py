#!/usr/bin/env python3
"""Customize an official Ubuntu Pi image without mounts, sudo, or Linux."""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import uuid
from pyfatfs.PyFatFS import PyFatFS

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA256 = 'd59ef9c6e85b906995501f166d00726ab80b59be7b65cdeac0fa31b996053e13'
BASE_URL = 'https://cdimage.ubuntu.com/releases/26.04/release/ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz'


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fat_offset(path):
    with open(path, 'rb') as f:
        mbr = f.read(512)
    if mbr[510:512] != b'\x55\xaa':
        raise ValueError('Missing MBR signature')
    for n in range(4):
        e = mbr[446 + n * 16:462 + n * 16]
        if e[4] in (0x0b, 0x0c):
            return struct.unpack_from('<I', e, 8)[0] * 512
    raise ValueError('No FAT32 boot partition in MBR')


def cloud_config(password_hash, ssh_key=None):
    user = {'name': 'gimx', 'groups': ['adm', 'sudo', 'input', 'dialout'],
            'shell': '/bin/bash', 'sudo': 'ALL=(ALL) NOPASSWD:ALL',
            'lock_passwd': False, 'passwd': password_hash}
    if ssh_key:
        user['ssh_authorized_keys'] = [ssh_key.strip()]
    files = []
    for name in ('gimx-install', 'gimx-bt', 'gimx-ps4', 'gimx-status'):
        files.append({'path': '/usr/local/sbin/' + name, 'permissions': '0755',
                      'owner': 'root:root', 'content': (ROOT / 'payload' / name).read_text()})
    files.append({'path': '/usr/local/share/gimx/patches/gimx-udp-status-v1.patch', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'patches/gimx-udp-status-v1.patch').read_text()})
    files.append({'path': '/usr/local/share/gimx/patches/gimx-ds4-usb-auth.patch', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'patches/gimx-ds4-usb-auth.patch').read_text()})
    files.append({'path': '/usr/local/share/gimx/patches/gimx-ds4-auth2.patch', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'patches/gimx-ds4-auth2.patch').read_text()})
    files.append({'path': '/usr/local/share/gimx/patches/gimx-ds4-software-auth.patch', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'patches/gimx-ds4-software-auth.patch').read_text()})
    files.append({'path': '/usr/local/share/gimx/gimx_status.py', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'payload/gimx_status.py').read_text()})
    files.append({'path': '/usr/local/share/gimx/LICENSE', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'LICENSE').read_text()})
    files.append({'path': '/usr/local/share/gimx/LICENSING.md', 'permissions': '0644',
                  'owner': 'root:root', 'content': (ROOT / 'LICENSING.md').read_text()})
    files.append({'path': '/root/.gimx/config/ps4-smoke.xml', 'permissions': '0600',
                  'owner': 'root:root', 'content': (ROOT / 'payload/ps4-smoke.xml').read_text()})
    files.append({'path': '/etc/motd', 'permissions': '0644', 'content':
                  'GIMX / Ubuntu 26.04.1 Pi 5 experimental image\n'
                  'Build status: sudo cloud-init status --long\n'
                  'Build log: /var/log/gimx/install.log\n'
                  'Bluetooth test: sudo gimx-bt inspect\n'})
    return {'hostname': 'gimx-pi5', 'manage_etc_hosts': True,
            'users': [user], 'ssh_pwauth': not bool(ssh_key), 'disable_root': True,
            'chpasswd': {'expire': False}, 'write_files': files,
            'runcmd': [['/usr/local/sbin/gimx-install']],
            'final_message': 'Cloud-init finished; check /var/lib/gimx/install.ready before testing.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, type=Path, help='Official compressed .img.xz')
    p.add_argument('--output', type=Path, default=ROOT / 'dist/ubuntu-26.04.1-gimx-pi5.img')
    p.add_argument('--ssh-key', type=Path, help='Optional public key; disables SSH passwords')
    a = p.parse_args()
    if a.output.exists():
        p.error('Output already exists; choose a new filename')
    print('Verifying official base SHA-256...', flush=True)
    if digest(a.base) != BASE_SHA256:
        p.error('Base SHA-256 differs from pinned Canonical release')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    password = secrets.token_urlsafe(24)
    password_hash = subprocess.run(['openssl', 'passwd', '-6', '-stdin'],
        input=password + '\n', text=True, capture_output=True, check=True).stdout.strip()
    config = cloud_config(password_hash, a.ssh_key.read_text() if a.ssh_key else None)
    user_data = '#cloud-config\n' + json.dumps(config, indent=2) + '\n'
    staging = a.output.with_suffix('.partial')
    if staging.exists():
        p.error('Staging file exists; inspect/remove it before retrying')
    print('Decompressing and injecting cloud-init...', flush=True)
    with lzma.open(a.base, 'rb') as src, staging.open('xb') as dest:
        shutil.copyfileobj(src, dest, 8 * 1024 * 1024)
    offset = fat_offset(staging)
    network = 'network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n      optional: false\n'
    metadata = 'dsmode: local\ninstance_id: gimx-' + str(uuid.uuid4()) + '\n'
    with PyFatFS(str(staging), offset=offset) as fs:
        for name, value in [('user-data', user_data), ('meta-data', metadata), ('network-config', network)]:
            if fs.exists(name):
                fs.remove(name)
            fs.writetext(name, value)
    with PyFatFS(str(staging), offset=offset, read_only=True) as fs:
        assert fs.readtext('user-data') == user_data
        assert fs.readtext('network-config') == network
        assert fs.readtext('meta-data') == metadata
    staging.rename(a.output)
    (a.output.parent / 'user-data').write_text(user_data)
    credentials = a.output.with_suffix('.credentials.txt')
    credentials.write_text('User: gimx\nPassword: ' + password + '\n'
                           'SSH: ssh gimx@<Pi Ethernet IP>\nChange password with passwd.\n')
    image_hash = digest(a.output)
    a.output.with_suffix('.img.sha256').write_text(image_hash + '  ' + a.output.name + '\n')
    manifest = {'base_url': BASE_URL, 'base_sha256': BASE_SHA256,
                'image_sha256': image_hash, 'image_bytes': a.output.stat().st_size,
                'gimx_commit': 'e44a1cc9823939165d3d92418e9d679c66496ee1',
                'status_patch_sha256': digest(ROOT / 'patches/gimx-udp-status-v1.patch'),
                'usb_auth_patch_sha256': digest(ROOT / 'patches/gimx-ds4-usb-auth.patch'),
                'auth2_patch_sha256': digest(ROOT / 'patches/gimx-ds4-auth2.patch'),
                'software_auth_patch_sha256': digest(ROOT / 'patches/gimx-ds4-software-auth.patch'),
                'provisioning': 'Online first-boot build; not precompiled',
                'validation': 'Checksum and FAT cloud-init readback; no hardware boot test'}
    a.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Image:', a.output)
    print('Login details (private):', credentials)


if __name__ == '__main__':
    main()
