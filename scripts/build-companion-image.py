#!/usr/bin/env python3
"""Build the Ubuntu Pi 4 pairing companion with the prebuilt static ARM64 daemon."""
import argparse
import base64
import importlib.util
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

spec=importlib.util.spec_from_file_location('base',Path(__file__).with_name('build-image.py'))
base=importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ROOT=base.ROOT


def config(binary, password_hash, ssh_key=None):
    user={'name':'gimx','groups':['adm','sudo'],'shell':'/bin/bash',
          'sudo':'ALL=(ALL) NOPASSWD:ALL','lock_passwd':False,'passwd':password_hash}
    if ssh_key:user['ssh_authorized_keys']=[ssh_key.strip()]
    files=[]
    def add(path,content,mode='0644',encoding=None):
        item={'path':path,'permissions':mode,'owner':'root:root','content':content}
        if encoding:item['encoding']=encoding
        files.append(item)
    add('/usr/local/libexec/gimx-pairing',base64.b64encode(binary.read_bytes()).decode(),'0755','b64')
    for name in ('start-pairing','install-pairing','pairing-status'):
        add('/usr/local/sbin/'+name,(ROOT/'companion'/name).read_text(),'0755')
    add('/etc/systemd/system/gimx-pairing.service',(ROOT/'companion/gimx-pairing.service').read_text())
    # Ship matching source and retained upstream notices alongside the GPL binary.
    for name in ('Makefile','NOTICE','COPYING','src/pairing.c','src/descriptors.h',
                 'src/generate-descriptors.py','vendor/EMUPS4PAIRING/emu.c',
                 'vendor/EMUPS4PAIRING/Descriptors.c','vendor/EMUPS4PAIRING/Descriptors.h'):
        add('/usr/local/src/gimx-pairing/'+name,(ROOT/'companion'/name).read_text())
    add('/etc/motd','GIMX Pi 4 USB pairing companion (experimental)\n'
        'Status: sudo pairing-status\nLogs: sudo journalctl -u gimx-pairing\n'
        'USB-C is the gadget connection. Host commands: ds4tool -t\n')
    return {'hostname':'gimx-pairing','manage_etc_hosts':True,'users':[user],
            'ssh_pwauth':not bool(ssh_key),'disable_root':True,
            'chpasswd':{'expire':False},'write_files':files,
            'runcmd':[['/usr/local/sbin/install-pairing']],
            'final_message':'Pairing companion initialized; check gimx-pairing.service.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--binary',type=Path,default=ROOT/'dist/companion/pairing-gadget')
    p.add_argument('--output',type=Path,default=ROOT/'dist/companion/ubuntu-26.04.1-gimx-pairing-pi4.img')
    p.add_argument('--ssh-key',type=Path)
    a=p.parse_args()
    raw=a.binary.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',raw,18)[0]!=183:
        p.error('Expected a little-endian ARM64 ELF binary')
    # Static image payload must not depend on a guest library installation.
    phoff=struct.unpack_from('<Q',raw,32)[0]
    entsize,num=struct.unpack_from('<HH',raw,54)
    if any(struct.unpack_from('<I',raw,phoff+i*entsize)[0]==3 for i in range(num)):
        p.error('Binary has a dynamic interpreter; build it with -static')
    if a.output.exists():p.error('Output already exists; choose a new path')
    if base.digest(a.base)!=base.BASE_SHA256:p.error('Official image checksum mismatch')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    os.umask(0o077)
    password=secrets.token_urlsafe(24)
    hashed=subprocess.run(['openssl','passwd','-6','-stdin'],input=password+'\n',
                          text=True,capture_output=True,check=True).stdout.strip()
    cfg=config(a.binary,hashed,a.ssh_key.read_text() if a.ssh_key else None)
    userdata='#cloud-config\n'+json.dumps(cfg,indent=2)+'\n'
    stage=a.output.with_suffix('.partial')
    if stage.exists():p.error('Partial build exists; inspect it before retrying')
    print('Decompressing verified Ubuntu Pi image...',flush=True)
    with lzma.open(a.base,'rb') as src,stage.open('xb') as dst:
        shutil.copyfileobj(src,dst,8*1024*1024)
    offset=base.fat_offset(stage)
    network='network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n      optional: true\n'
    metadata='dsmode: local\ninstance_id: pairing-'+str(uuid.uuid4())+'\n'
    with PyFatFS(str(stage),offset=offset) as fs:
        config_name=next(n for n in fs.listdir('/') if n.lower()=='config.txt')
        original=fs.readtext(config_name)
        if original.count('\ndtoverlay=dwc2\n')!=1:
            raise RuntimeError('Unexpected base dwc2 configuration; inspect before changing it')
        bootconfig=original.replace('\ndtoverlay=dwc2\n','\ndtoverlay=dwc2,dr_mode=peripheral\n')
        changes={'user-data':userdata,'meta-data':metadata,'network-config':network,config_name:bootconfig}
        for name,value in changes.items():
            if fs.exists(name):fs.remove(name)
            fs.writetext(name,value)
    with PyFatFS(str(stage),offset=offset,read_only=True) as fs:
        for name,value in changes.items():assert fs.readtext(name)==value,name
    stage.rename(a.output)
    (a.output.parent/'user-data').write_text(userdata)
    creds=a.output.with_suffix('.credentials.txt')
    creds.write_text('User: gimx\nPassword: '+password+'\nSSH: ssh gimx@<Pi4 Ethernet IP>\n')
    sha=base.digest(a.output)
    a.output.with_suffix('.img.sha256').write_text(sha+'  '+a.output.name+'\n')
    manifest={'base_url':base.BASE_URL,'base_sha256':base.BASE_SHA256,'image_sha256':sha,
              'daemon_sha256':base.digest(a.binary),'image_bytes':a.output.stat().st_size,
              'firmware_reference':'ab2215ebad5d50fd25b6b40ac74254f1f4c0bda5',
              'provisioning':'Precompiled static ARM64 daemon; no first-boot package installation',
              'validation':'QEMU HVF + dummy_hcd + unmodified ds4tool; PS4 and physical Pi 4 untested',
              'sources':{str(f.relative_to(ROOT)):base.digest(f) for f in (ROOT/'companion').rglob('*')
                         if f.is_file() and '__pycache__' not in str(f)}}
    a.output.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Image:',a.output)
    print('Login details:',creds)


if __name__=='__main__':main()
