#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
from pyfatfs.PyFat import PyFat
from pyfatfs.PyFatFS import PyFatFS
spec = importlib.util.spec_from_file_location('build', Path(__file__).with_name('build-image.py'))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
root = build.ROOT
seed = root / '.vm/seed.img'
seed.parent.mkdir(exist_ok=True)
config = build.cloud_config('!', (root / '.vm/id_ed25519.pub').read_text())
config['users'][0]['lock_passwd'] = True
config['runcmd'] = [['bash', '-c', '/usr/local/sbin/gimx-install; rc=$?; echo GIMX_QEMU_BUILD_RESULT=$rc > /dev/ttyAMA0; exit $rc']]
seed.touch()
f = PyFat()
f.mkfs(str(seed), fat_type=PyFat.FAT_TYPE_FAT16, size=32*1024*1024, label='CIDATA')
f.close()
with PyFatFS(str(seed)) as fs:
    fs.writetext('user-data', '#cloud-config\n' + json.dumps(config))
    fs.writetext('meta-data', 'instance-id: gimx-build-check-1\nlocal-hostname: gimx-build-check\n')
print(seed)
