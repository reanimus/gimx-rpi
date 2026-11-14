#!/usr/bin/env python3
"""Read-only verification of the finished companion image against its official base."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from pyfatfs.PyFatFS import PyFatFS
ROOT=Path(__file__).resolve().parents[2]
image=ROOT/'dist/companion/ubuntu-26.04.1-gimx-pairing-pi4.img'
base=Path('/tmp/gimx-ubuntu-base.img')
with base.open('rb') as f: mbr=f.read(512)
start,sectors=struct.unpack_from('<II',mbr,454)
offset=start*512;end=(start+sectors)*512
with base.open('rb') as a,image.open('rb') as b:
    assert a.read(offset)==b.read(offset)
    a.seek(end);b.seek(end)
    while True:
        x=a.read(8*1024*1024);y=b.read(8*1024*1024)
        assert x==y,'Data outside FAT changed'
        if not x:break
with PyFatFS(str(base),offset=offset,read_only=True) as a,PyFatFS(str(image),offset=offset,read_only=True) as b:
    for path in a.walk.files():
        if path.lower() not in ('/config.txt','/user-data','/meta-data','/network-config'):
            assert a.readbytes(path)==b.readbytes(path),path
    assert b.readtext('CONFIG.TXT')==a.readtext('CONFIG.TXT').replace('\ndtoverlay=dwc2\n','\ndtoverlay=dwc2,dr_mode=peripheral\n')
    cfg=json.loads(b.readtext('user-data').split('\n',1)[1])
    for item in cfg['write_files']:
        if item.get('encoding')=='b64':
            assert base64.b64decode(item['content'])==(ROOT/'dist/companion/pairing-gadget').read_bytes()
        elif item['path'].startswith('/usr/local/src/gimx-pairing/'):
            relative=item['path'].split('/usr/local/src/gimx-pairing/',1)[1]
            assert item['content']==(ROOT/'companion'/relative).read_text()
        elif item['path']!='/etc/motd':
            assert item['content']==(ROOT/'companion'/Path(item['path']).name).read_text()
    assert not any('state.bin' in i['path'] for i in cfg['write_files'])
    assert 'optional: true' in b.readtext('network-config')
checksum=hashlib.sha256()
with image.open('rb') as f:
    for block in iter(lambda:f.read(8*1024*1024),b''):checksum.update(block)
expected=image.with_suffix('.img.sha256').read_text().split()[0]
assert checksum.hexdigest()==expected
log=(ROOT/'dist/companion/qemu-verification.log').read_text()
daemon_sha=hashlib.sha256((ROOT/'dist/companion/pairing-gadget').read_bytes()).hexdigest()
assert daemon_sha in log,'Image daemon differs from QEMU-tested binary'
print('PASS: checksum; exact tested daemon and source; no pairing credentials preloaded; peripheral overlay; preserved base outside intended boot-file edits')
