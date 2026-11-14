"""Independent wire/crypto tests. No controller material embedded or printed."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DRIVER = Path(__file__).resolve().with_name('software-driver')


def crc(packet):
    return packet + struct.pack('<I', zlib.crc32(packet))


def pages(nonce, sequence):
    return [crc(bytes((0x53, 0xf0, sequence, page, 0)) +
                nonce[page*56:(page+1)*56].ljust(56, b'\0')) for page in range(5)]


class Session:
    def __init__(self, directory, table=None):
        env = dict(os.environ)
        for name in ('GIMX_DS4_USB', 'GIMX_DS4_AUTH2_TABLE'):
            env.pop(name, None)
        env['GIMX_DS4_KEYS'] = str(directory)
        if table:
            env['GIMX_DS4_AUTH2_TABLE'] = str(table)
        self.process = subprocess.Popen([str(DRIVER)], env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def request(self, packet):
        self.process.stdin.write(struct.pack('<H', len(packet)) + packet)
        self.process.stdin.flush()
        size = self.process.stdout.read(2)
        if len(size) != 2:
            raise AssertionError('Authentication test driver stopped unexpectedly')
        length, = struct.unpack('<H', size)
        response = self.process.stdout.read(length)
        assert len(response) == length, 'Short reply'
        return response

    def close(self, success=True):
        if self.process.stdin:
            self.process.stdin.close()
            self.process.stdin = None
        out, err = self.process.communicate(timeout=10)
        # Do not print wire payloads or user material even when a test fails.
        assert not out, 'Unconsumed wire reply'
        assert self.process.returncode == (0 if success else 2), 'Unexpected driver exit code'
        assert b'Sanitizer' not in err and b'runtime error:' not in err, 'Sanitizer failure'


class SoftwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='gimx-software-test-')
        cls.directory = Path(cls.temporary.name)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (cls.directory/'key.pem').write_bytes(cls.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        (cls.directory/'serial.txt').write_text('0123456789abcdef\n')
        (cls.directory/'sig.bin').write_bytes(os.urandom(256))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def session(self, directory=None, table=None):
        s = Session(directory or self.directory, table)
        self.addCleanup(s.close)
        return s

    def check_crc(self, response):
        self.assertTrue(len(response) >= 5 and zlib.crc32(response[:-4]) ==
                        int.from_bytes(response[-4:], 'little'), 'Wire CRC mismatch')

    def sign(self, s, nonce, sequence, directory=None, interleave=False):
        for packet in pages(nonce, sequence):
            self.assertTrue(s.request(packet) == b'\0', 'F0 was not accepted')
            if interleave:
                self.auth2(s)
        status = s.request(b'\x4b\xf2\x10\0')
        self.check_crc(status)
        self.assertTrue(status[:4] == bytes((0xa3,0xf2,sequence,0)), 'Not ready or wrong sequence')
        response = bytearray()
        for page in range(19):
            part = s.request(b'\x4b\xf1\x40\0')
            self.assertTrue(len(part) == 65, 'Wrong F1 size')
            self.check_crc(part)
            self.assertTrue(part[:5] == bytes((0xa3,0xf1,sequence,page,0)), 'Wrong F1 sequence/page')
            response += part[5:61]
        directory = Path(directory or self.directory)
        key = serialization.load_pem_private_key((directory/'key.pem').read_bytes(), None)
        public = key.public_key().public_numbers()
        self.assertTrue(int.from_bytes(response[272:528], 'big') == public.n, 'Wrong modulus byte order')
        self.assertTrue(int.from_bytes(response[528:784], 'big') == public.e, 'Wrong exponent byte order')
        serial = bytes.fromhex((directory/'serial.txt').read_text().strip()).rjust(16, b'\0')
        self.assertTrue(response[256:272] == serial, 'Wrong serial padding')
        self.assertTrue(response[784:1040] == (directory/'sig.bin').read_bytes(), 'Certificate changed')
        self.assertTrue(response[1040:] == bytes(24), 'Wrong response padding')
        key.public_key().verify(bytes(response[:256]), nonce,
                                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                                hashes.SHA256())
        self.assertTrue(s.request(b'\x43\xf1') == b'\x01', 'Read past final page must not return stale data')
        return bytes(response[:256])

    def auth2(self, s):
        challenge = os.urandom(32)
        self.assertTrue(s.request(crc(b'\x53\x03\xfe\x07'+challenge)) == b'\0')
        reply = s.request(b'\x4b\x04\x2f\0')
        self.assertTrue(len(reply) == 48)
        self.check_crc(reply)
        secret = bytes(range(224,256))  # Synthetic slot 7; not controller data.
        self.assertTrue(reply[4:24] == hashlib.sha1(secret+challenge+reply[24:44]).digest())

    def test_fresh_nonce_pss_paging_and_auth2_interleaving(self):
        self.sign(self.session(), os.urandom(256), 255, interleave=True)

    def test_repeated_challenges_and_sequence_wrap(self):
        s = self.session()
        nonce = os.urandom(256)
        sig1 = self.sign(s, nonce, 255)
        sig2 = self.sign(s, nonce, 0)
        self.assertTrue(sig1 != sig2, 'PSS salt must be randomized')

    def test_idle_incomplete_and_new_challenge_clear_stale_response(self):
        s = self.session()
        self.assertTrue(s.request(b'\x43\xf1') == b'\x01')
        for packet in pages(os.urandom(256), 1): s.request(packet)
        self.assertTrue(len(s.request(b'\x43\xf1')) == 65)
        s.request(pages(os.urandom(256), 2)[0])
        self.assertTrue(s.request(b'\x43\xf1') == b'\x01')
        self.assertTrue(s.request(b'\x43\xf2')[2:4] == b'\x02\x10')

    def test_malformed_out_of_order_and_mixed_sequence(self):
        s = self.session()
        p = pages(os.urandom(256), 1)
        invalid = [p[2], p[0][:-1], p[0][:-1]+bytes((p[0][-1]^1,)),
                   crc(b'\x53\xf0\x01\x05\0'+bytes(56)),
                   crc(b'\x53\xf0\x01\0\x01'+bytes(56))]
        for packet in invalid:
            for valid in p: s.request(valid)
            self.assertTrue(s.request(packet) == b'\x04', 'Malformed F0 accepted')
            self.assertTrue(s.request(b'\x43\xf1') == b'\x01', 'Stale response remained')
        s.request(p[0])
        self.assertTrue(s.request(pages(os.urandom(256), 2)[1]) == b'\x04')

    def test_retries_and_undersized_get_do_not_advance(self):
        s = self.session()
        for packet in pages(os.urandom(256), 3):
            self.assertTrue(s.request(packet) == b'\0')
            self.assertTrue(s.request(packet) == b'\0')
        self.assertTrue(s.request(b'\x4b\xf1\x3f\0') == b'\x04')
        self.assertTrue(s.request(b'\x43\xf1')[2:5] == b'\x03\0\0')

    def test_metadata_reports_and_unsupported_pairing_writes(self):
        s = self.session()
        for report, size in ((2,38),(5,42),(6,54),(0xa3,50)):
            reply = s.request(bytes((0x43, report)))
            self.assertTrue(len(reply) == size and reply[:2] == bytes((0xa3,report)))
            if report in (5,6): self.check_crc(reply)
        for report in (0x13,0x14):
            self.assertTrue(s.request(bytes((0x53,report))) == b'\x03')

    def test_bad_credentials_fail_before_any_request(self):
        for bad in ('key.pem', 'serial.txt', 'sig.bin', 'public-only', 'encrypted', 'rsa1024', 'ec', 'missing'):
            with self.subTest(kind=bad), tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)
                for name in ('key.pem','serial.txt','sig.bin'): shutil.copyfile(self.directory/name, dest/name)
                if bad == 'missing': (dest/'sig.bin').unlink()
                elif bad == 'public-only': (dest/'key.pem').write_bytes(self.key.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
                elif bad == 'encrypted': (dest/'key.pem').write_bytes(self.key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(b'test-only')))
                elif bad in ('rsa1024','ec'):
                    from cryptography.hazmat.primitives.asymmetric import ec
                    key = rsa.generate_private_key(public_exponent=65537, key_size=1024) if bad == 'rsa1024' else ec.generate_private_key(ec.SECP256R1())
                    (dest/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                else: (dest/bad).write_bytes(b'not valid')
                Session(dest).close(success=False)

    def test_full_serial_and_pkcs8(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest/'key.pem').write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            (dest/'serial.txt').write_text('00112233445566778899aabbccddeeff\r\n')
            shutil.copyfile(self.directory/'sig.bin', dest/'sig.bin')
            s = Session(dest)
            try: self.sign(s, os.urandom(256), 4, dest)
            finally: s.close()

    @unittest.skipUnless(os.environ.get('GIMX_TEST_DS4_KEYS'), 'private material not supplied')
    def test_user_material_full_worker_roundtrip(self):
        directory = Path(os.environ['GIMX_TEST_DS4_KEYS'])
        table = os.environ.get('GIMX_TEST_AUTH2_TABLE')
        self.assertTrue(bool(table), 'Real table is required for this private integration test')
        s = self.session(directory, table)
        self.sign(s, os.urandom(256), 9, directory)
        self.sign(s, os.urandom(256), 10, directory)
        # Verify AUTH2 with the supplied table, keeping every byte private.
        challenge = os.urandom(32)
        self.assertTrue(s.request(crc(b'\x53\x03\x08\0'+challenge)) == b'\0')
        reply = s.request(b'\x43\x04')
        self.check_crc(reply)
        secret = Path(table).read_bytes()[:32]
        self.assertTrue(reply[4:24] == hashlib.sha1(secret+challenge+reply[24:44]).digest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
