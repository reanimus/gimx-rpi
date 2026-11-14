import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_probe', ROOT / 'scripts/ds4-usb-auth-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Donor:
    def __init__(self, response=None):
        self.writes = []
        self.page = 0
        self.sequence = 7
        self.status = 0
        self.response = response or bytes(i % 256 for i in range(1064))
        self.bad_crc = False
        self.bad_page = False

    def set(self, data):
        self.writes.append(data)

    def get(self, report, size):
        if report == 0xf2:
            return probe.with_crc(bytes((report, self.sequence, self.status)) + bytes(9))
        index = self.page
        self.page += 1
        result = probe.with_crc(bytes((report, self.sequence, index + int(self.bad_page), 0)) +
                                self.response[index * 56:(index + 1) * 56])
        if self.bad_crc:
            result = result[:-1] + bytes((result[-1] ^ 1,))
        return result


class ProbeTests(unittest.TestCase):
    def run_challenge(self, donor):
        return probe.collect_response(donor, bytes(range(256)), 7, sleep=lambda _: None)

    def test_complete_exchange_and_nonce_paging(self):
        donor = Donor()
        self.assertEqual(self.run_challenge(donor), donor.response)
        self.assertEqual(len(donor.writes), 5)
        self.assertEqual(donor.page, 19)
        self.assertEqual(b''.join(p[4:60] for p in donor.writes), bytes(range(256)) + bytes(24))
        for index, data in enumerate(donor.writes):
            self.assertEqual(len(data), 64)
            self.assertEqual(data[:4], bytes((0xf0, 7, index, 0)))
            probe.check_crc(data)

    def test_signing_error(self):
        donor = Donor()
        donor.status = 1
        with self.assertRaisesRegex(probe.ProbeError, 'signing status'):
            self.run_challenge(donor)

    def test_stale_sequence(self):
        donor = Donor()
        donor.sequence = 8
        with self.assertRaisesRegex(probe.ProbeError, 'sequence'):
            self.run_challenge(donor)

    def test_corrupt_page(self):
        donor = Donor()
        donor.bad_crc = True
        with self.assertRaisesRegex(probe.ProbeError, 'CRC'):
            self.run_challenge(donor)

    def test_out_of_order_page(self):
        donor = Donor()
        donor.bad_page = True
        with self.assertRaisesRegex(probe.ProbeError, 'page mismatch'):
            self.run_challenge(donor)

    def test_busy_timeout(self):
        donor = Donor()
        donor.status = 0x10
        ticks = iter((0, 0.1, 0.2, 1.1))
        with self.assertRaisesRegex(probe.ProbeError, 'Timed out'):
            probe.collect_response(donor, bytes(256), 7, timeout=1,
                                   clock=lambda: next(ticks), sleep=lambda _: None)

    def test_not_ready_then_ready(self):
        donor = Donor()
        donor.status = 0x10
        def sleep(seconds):
            if seconds == 0.1:
                donor.status = 0
        self.assertEqual(probe.collect_response(donor, bytes(256), 7, sleep=sleep), donor.response)

    def test_discovery_bus_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, identity in [('hidraw0', '0005:0000054C:000009CC'),
                                   ('hidraw1', '0003:0000054C:000009CC')]:
                (root / name / 'device').mkdir(parents=True)
                (root / name / 'device/uevent').write_text('HID_ID=' + identity + '\n')
            self.assertEqual(probe.discover(root), '/dev/hidraw1')
            (root / 'hidraw2/device').mkdir(parents=True)
            (root / 'hidraw2/device/uevent').write_text('HID_ID=0003:0000054C:000005C4\n')
            with self.assertRaisesRegex(probe.ProbeError, 'found 2'):
                probe.discover(root)

    def test_wrong_bus_rejected_and_fd_closed(self):
        def ioctl(fd, request, data, mutate):
            data[:] = struct.pack('=IHH', 5, 0x054c, 0x09cc)
            return 0
        with patch.object(probe.os, 'open', return_value=12), \
                patch.object(probe.os, 'close') as close, patch.object(probe.fcntl, 'ioctl', side_effect=ioctl):
            with self.assertRaisesRegex(probe.ProbeError, 'not a USB'):
                probe.Hidraw('/dev/hidraw0')
            close.assert_called_once_with(12)

    def test_get_short_and_wrong_id_rejected(self):
        donor = object.__new__(probe.Hidraw)
        donor.fd = 12
        with patch.object(probe.fcntl, 'ioctl', return_value=15):
            with self.assertRaisesRegex(probe.ProbeError, 'length/ID'):
                donor.get(0xf2, 16)
        def ioctl(fd, request, data, mutate):
            data[0] = 0xf1
            return len(data)
        with patch.object(probe.fcntl, 'ioctl', side_effect=ioctl):
            with self.assertRaisesRegex(probe.ProbeError, 'length/ID'):
                donor.get(0xf2, 16)


class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # An independent signer checks that the probe verifies the actual nonce,
        # not merely plausible-looking F1 data. No device keys are used.
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa, padding
            from cryptography.hazmat.primitives import hashes
        except ImportError:
            raise unittest.SkipTest('cryptography needed to generate independent test signatures')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = key.public_key().public_numbers()
        cls.nonce = bytes(range(256))
        signature = key.sign(cls.nonce, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                             hashes.SHA256())
        cls.response = (signature + bytes(16) + public.n.to_bytes(256, 'big') +
                        public.e.to_bytes(256, 'big') + bytes(280))

    def test_valid_signature(self):
        probe.verify_response(self.nonce, self.response)

    def test_wrong_nonce(self):
        with self.assertRaisesRegex(probe.ProbeError, 'signature verification failed'):
            probe.verify_response(bytes(reversed(self.nonce)), self.response)

    def test_corrupt_signature(self):
        response = bytes((self.response[0] ^ 1,)) + self.response[1:]
        with self.assertRaisesRegex(probe.ProbeError, 'signature verification failed'):
            probe.verify_response(self.nonce, response)

    def test_empty_public_key(self):
        with self.assertRaisesRegex(probe.ProbeError, 'public key'):
            probe.verify_response(self.nonce, bytes(1064))


if __name__ == '__main__':
    unittest.main()
