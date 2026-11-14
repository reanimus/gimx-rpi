#!/usr/bin/env python3
"""Integration test, ONLY for a QEMU guest with dummy_hcd and pairing-debug service."""
import ctypes as c
from pathlib import Path
import subprocess
import time
import tempfile

assert Path('/sys/class/udc/dummy_udc.0').exists(), 'Refusing to run without simulated UDC'
sony = [p for p in Path('/sys/bus/usb/devices').glob('*') if (p/'idVendor').exists()
        and (p/'idVendor').read_text().strip() == '054c'
        and (p/'idProduct').read_text().strip() == '05c4']
assert len(sony) == 1 and 'dummy_hcd' in str(sony[0].resolve()), 'Refusing to address a physical controller'
lib = c.CDLL('libusb-1.0.so.0')
lib.libusb_init.argtypes=[c.POINTER(c.c_void_p)]
lib.libusb_open_device_with_vid_pid.argtypes=[c.c_void_p,c.c_uint16,c.c_uint16]
lib.libusb_open_device_with_vid_pid.restype=c.c_void_p
lib.libusb_set_auto_detach_kernel_driver.argtypes=[c.c_void_p,c.c_int]
lib.libusb_claim_interface.argtypes=[c.c_void_p,c.c_int]
lib.libusb_release_interface.argtypes=[c.c_void_p,c.c_int]
lib.libusb_close.argtypes=[c.c_void_p]
lib.libusb_exit.argtypes=[c.c_void_p]
lib.libusb_reset_device.argtypes=[c.c_void_p]
lib.libusb_control_transfer.argtypes=[c.c_void_p,c.c_uint8,c.c_uint8,c.c_uint16,c.c_uint16,c.c_void_p,c.c_uint16,c.c_uint]
ctx=c.c_void_p()
assert lib.libusb_init(c.byref(ctx)) == 0

def connect():
    h=lib.libusb_open_device_with_vid_pid(ctx,0x054c,0x05c4)
    assert h
    assert lib.libusb_set_auto_detach_kernel_driver(h,1)==0
    assert lib.libusb_claim_interface(h,0)==0
    return h

def close(h):
    lib.libusb_release_interface(h,0)
    lib.libusb_close(h)

def transfer(h,rt,req,val,payload=0,index=0):
    n=payload if isinstance(payload,int) else len(payload)
    buf=c.create_string_buffer(n or 1)
    if not isinstance(payload,int):buf.raw=bytes(payload)
    rc=lib.libusb_control_transfer(h,rt,req,val,index,buf,n,3000)
    return rc,buf.raw[:max(rc,0)]

def read(h,val,n):
    rc,data=transfer(h,0xa1,1,val,n)
    assert rc>=0,(hex(val),rc)
    return data

def write(h,val,data):
    rc,_=transfer(h,0x21,9,val,data)
    assert rc==len(data),(hex(val),rc)

h=connect()
try:
    assert transfer(h,0x81,6,0x2200,1793)[0]==483
    assert transfer(h,0x81,6,0x2200,8)[0]==8
    assert len(read(h,0x03a3,49))==49
    assert len(read(h,0x0302,37))==37
    local=bytes.fromhex('BC9A78563412')
    peer=bytes.fromhex('665544332211')
    key=bytes.fromhex('00112233445566778899AABBCCDDEEFF')
    write(h,0x0312,local)
    assert read(h,0x0312,16)[1:7]==local
    # Emulate the PS4's SET_REPORT; these are synthetic test credentials.
    write(h,0x0313,b'\x13'+peer+key)
    assert read(h,0x0312,16)[10:16]==peer
    assert read(h,0x0313,16)==key
    assert read(h,0x0313,4)==key[:4]
    for val,body in [(0x0312,b'12345'),(0x0313,bytes(22)),(0x0399,bytes(23))]:
        assert transfer(h,0x21,9,val,body)[0]==-9, 'Malformed request must stall'
    assert transfer(h,0xa1,1,0x0399,16)[0]==-9
    assert transfer(h,0xa1,1,0x0313,16,index=1)[0]<0  # Host may reject unclaimed interface first.
    write(h,0x0314,bytes([0x14,0]))
    assert read(h,0x0313,16)==key
finally:
    close(h)
print('PASS: descriptors, ds4 feature reads, PS4 credential write, bounds checks, stalls')
h=connect()
assert lib.libusb_reset_device(h)==0
close(h)
time.sleep(2)
h=connect()
try:
    assert read(h,0x0313,16)==key
finally:close(h)
print('PASS: USB bus reset and reconfiguration preserve the pairing')
# Repeat SET_CONFIGURATION with interrupt requests outstanding, then reset again.
for iteration in range(20):
    h=connect()
    try:
        assert transfer(h,0,9,1,0)[0]==0, 'Repeated configuration failed'
        assert read(h,0x0313,16)==key
        assert lib.libusb_reset_device(h)==0
    finally: close(h)
print('PASS: 20 repeated configurations and bus resets with active endpoint workers')

# State is fsynced before the subsequent GET_REPORT is handled.
subprocess.run(['systemctl','restart','pairing-debug'],check=True)
time.sleep(2)
h=connect()
try:
    assert read(h,0x0312,16)[1:7]==local
    assert read(h,0x0312,16)[10:16]==peer
    assert read(h,0x0313,16)==key
finally: close(h)
print('PASS: credentials survive daemon stop/re-enumeration/restart')
result=subprocess.run(['ds4tool','-t'],capture_output=True,text=True,check=True)
assert '11:22:33:44:55:66' in result.stdout
assert '12:34:56:78:9A:BC' in result.stdout
assert key.hex().upper() in result.stdout.upper()
print('PASS: unmodified ds4tool -t reads synthetic console address and exact 16-byte key')
subprocess.run(['ds4tool','-t','-m','00:00:00:00:00:00'],stdout=subprocess.DEVNULL,check=True)
h=connect()
try:
    assert read(h,0x0313,16)==bytes(16)
    assert read(h,0x0312,16)[10:16]==bytes(6)
    assert read(h,0x0312,16)[1:7]==local
finally:close(h)
print('PASS: ds4tool reset clears console/key and preserves configured Pi 5 address')
lib.libusb_exit(ctx)

with tempfile.TemporaryDirectory() as directory:
    p=Path(directory)/'state.bin'
    p.write_bytes(b'corrupt')
    binary=Path(__file__).resolve().parents[1]/'pairing-gadget'
    result=subprocess.run([str(binary),'dummy_udc','dummy_udc.0',directory],capture_output=True,timeout=5)
    assert result.returncode!=0 and b'Invalid state file' in result.stderr
    assert p.read_bytes()==b'corrupt'
print('PASS: corrupt persistent state fails closed without overwriting it')
