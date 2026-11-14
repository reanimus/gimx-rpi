#!/usr/bin/env python3
"""Update only our existing build's payload, preserving its login credentials."""
import importlib.util
import json
from pathlib import Path
from pyfatfs.PyFatFS import PyFatFS
spec = importlib.util.spec_from_file_location('build', Path(__file__).with_name('build-image.py'))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
p = build.ROOT / 'dist/ubuntu-26.04.1-gimx-pi5.img'
with PyFatFS(str(p), offset=build.fat_offset(p)) as fs:
    cfg = json.loads(fs.readtext('user-data').split('\n', 1)[1])
    cfg['write_files'] = build.cloud_config('unused')['write_files']
    data = '#cloud-config\n' + json.dumps(cfg, indent=2) + '\n'
    fs.remove('user-data')
    fs.writetext('user-data', data)
with PyFatFS(str(p), offset=build.fat_offset(p), read_only=True) as fs:
    assert fs.readtext('user-data') == data
(p.parent / 'user-data').write_text(data)
sha = build.digest(p)
p.with_suffix('.img.sha256').write_text(sha + '  ' + p.name + '\n')
manifest_path = p.with_suffix('.manifest.json')
manifest = json.loads(manifest_path.read_text())
manifest['image_sha256'] = sha
manifest['status_patch_sha256'] = build.digest(build.ROOT / 'patches/gimx-udp-status-v1.patch')
manifest['usb_auth_patch_sha256'] = build.digest(build.ROOT / 'patches/gimx-ds4-usb-auth.patch')
manifest['auth2_patch_sha256'] = build.digest(build.ROOT / 'patches/gimx-ds4-auth2.patch')
manifest['software_auth_patch_sha256'] = build.digest(build.ROOT / 'patches/gimx-ds4-software-auth.patch')
manifest['payload_sha256'] = {s.name: build.digest(s) for s in (build.ROOT / 'payload').iterdir() if s.is_file()}
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
print('Refreshed image and checksum; credentials preserved')
