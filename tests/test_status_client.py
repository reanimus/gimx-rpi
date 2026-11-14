import importlib.util
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'payload'))
import gimx_status as api


class DecodeTests(unittest.TestCase):
    def packet(self,flags=31,result=0,nonce=17,age=0xFFFFFFFF,version=1):
        return struct.pack('!BBBBIII',2,version,result,5,nonce,flags,age)

    def test_connected_and_pending(self):
        self.assertTrue(api.decode_reply(self.packet(),17).ready)
        p=api.decode_reply(self.packet(flags=1|8|16|32|64),17)
        self.assertFalse(p.ps4_connected)
        self.assertTrue(p.ds4_connected)
        self.assertFalse(p.ready)

    def test_no_reply_is_not_a_disconnected_snapshot(self):
        self.assertIsNone(api.decode_reply(b'',17))
        self.assertIsNone(api.decode_reply(self.packet(nonce=18),17))
        self.assertFalse(api.decode_reply(self.packet(flags=1),17).ready)

    def test_shutdown_and_unsupported(self):
        self.assertFalse(api.decode_reply(self.packet(flags=31|128),17).ready)
        value=api.decode_reply(self.packet(flags=31,result=1),17)
        self.assertFalse(value.supported)
        self.assertFalse(value.ready)

    def test_age_and_version(self):
        self.assertIsNone(api.decode_reply(self.packet(),17).last_report_age_ms)
        self.assertEqual(api.decode_reply(self.packet(flags=31|256,age=23),17).last_report_age_ms,23)
        with self.assertRaises(api.StatusProtocolError):
            api.decode_reply(self.packet(version=2),17)
        with self.assertRaises(api.StatusProtocolError):
            api.decode_reply(self.packet(result=2),17)

    def test_usb_donor_status_is_not_console_authentication(self):
        flags = 7 | api.USB_AUTH | api.USB_CONNECTED | api.USB_UNSUPPORTED
        status = api.decode_reply(self.packet(flags=flags),17)
        self.assertTrue(status.ready)
        self.assertTrue(status.usb_auth_unsupported)
        self.assertFalse(status.ds4_control_connected)
        self.assertEqual(status.to_dict()['auth_transport'], 'usb')
        self.assertFalse(api.decode_reply(self.packet(flags=flags & ~api.USB_CONNECTED),17).ready)
        self.assertFalse(api.decode_reply(self.packet(flags=flags | api.SHUTDOWN),17).ready)

    def test_software_ready_without_controller(self):
        flags = 7 | api.SOFTWARE_AUTH | api.SOFTWARE_READY
        value = api.decode_reply(self.packet(flags=flags), 17)
        self.assertEqual(value.auth_transport, 'software')
        self.assertTrue(value.ready and value.auth_ready)
        self.assertFalse(value.ds4_connected or value.usb_auth_connected or value.usb_auth_mode)
        self.assertFalse(api.decode_reply(self.packet(flags=flags & ~api.SOFTWARE_READY), 17).ready)
        self.assertFalse(api.decode_reply(self.packet(flags=flags | api.SHUTDOWN), 17).ready)

    def test_validation(self):
        with self.assertRaises(ValueError): api.query_status(timeout=0)
        with self.assertRaises(ValueError): api.query_status(port=0)


class UDPTests(unittest.TestCase):
    def test_loopback_nonce_filter(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1',0))
            server.settimeout(2)
            errors=[]
            def responder():
                try:
                    data,peer=server.recvfrom(64)
                    kind,version,nonce=struct.unpack('!BBI',data)
                    assert (kind,version)==(2,1)
                    server.sendto(struct.pack('!BBBBIII',2,1,0,5,nonce^1,31,0xFFFFFFFF),peer)
                    server.sendto(struct.pack('!BBBBIII',2,1,0,5,nonce,31|256,12),peer)
                except Exception as exc:errors.append(exc)
            worker=threading.Thread(target=responder)
            worker.start()
            status=api.query_status(port=server.getsockname()[1],timeout=1)
            worker.join(timeout=2)
            self.assertFalse(errors)
            self.assertTrue(status.ready)
            self.assertEqual(status.last_report_age_ms,12)

    def test_old_server_silence_times_out(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1',0))
            with self.assertRaises(api.StatusUnavailable):
                api.query_status(port=server.getsockname()[1],timeout=0.05)


if __name__=='__main__':unittest.main()
