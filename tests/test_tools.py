import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('bt', str(ROOT / 'payload/gimx-bt'))
spec = importlib.util.spec_from_loader(loader.name, loader)
bt = importlib.util.module_from_spec(spec)
loader.exec_module(bt)
ps4loader = importlib.machinery.SourceFileLoader('ps4', str(ROOT / 'payload/gimx-ps4'))
ps4spec = importlib.util.spec_from_loader(ps4loader.name, ps4loader)
ps4 = importlib.util.module_from_spec(ps4spec)
ps4loader.exec_module(ps4)


class HciTests(unittest.TestCase):
    def test_kernel_address_without_sysfs_attribute(self):
        output = ('hci0:   Type: Primary  Bus: UART\n'
                  '        BD Address: 12:34:56:78:9a:bc  ACL MTU: 1021:8  SCO MTU: 64:1\n'
                  '        UP RUNNING\n')
        with patch.object(bt, 'run', return_value=output) as run:
            self.assertEqual(bt.cached('hci0'), '12:34:56:78:9A:BC')
            run.assert_called_once_with('hciconfig', 'hci0')
        with patch.object(ps4.subprocess, 'check_output', return_value=output) as run:
            self.assertEqual(ps4.adapter_address('hci0'), '12:34:56:78:9A:BC')
            run.assert_called_once_with(['hciconfig', 'hci0'], text=True)

    def test_missing_kernel_address_is_not_success(self):
        with patch.object(bt, 'run', return_value='No such device'):
            with self.assertRaises(RuntimeError):
                bt.cached('hci0')
        with patch.object(ps4.subprocess, 'check_output', return_value=''):
            with self.assertRaises(ValueError):
                ps4.adapter_address('hci0')

    def test_vendor_byte_order(self):
        self.assertEqual(bt.vendor_bytes('12:34:56:78:9a:bc'), ['BC', '9A', '78', '56', '34', '12'])

    def test_reject_bad_addresses(self):
        for value in ('', 'AA:BB', '00:00:00:00:00:00', 'FF:FF:FF:FF:FF:FF', '12:34:56:78:9A:GG'):
            with self.assertRaises(ValueError):
                bt.address(value)

    def test_read_address_response(self):
        text = '< HCI Event: 0x0e plen 10\n  01 09 10 00 BC 9A 78 56 34 12\n'
        self.assertEqual(bt.completion(text, 0x1009), bytes.fromhex('BC9A78563412'))

    def test_pi5_actual_hcitool_transcript(self):
        text = ('< HCI Command: ogf 0x04, ocf 0x0009, plen 0\n'
                '> HCI Event: 0x0e plen 10\n'
                '  01 09 10 00 8A 6A 6A 9E A2 88\n')
        self.assertEqual(bt.completion(text, 0x1009), bytes.fromhex('8A6A6A9EA288'))

    def test_event_boundaries_and_lengths(self):
        for text in ('> HCI Event: 0x0e plen 10\n  01 09 10 00\n',
                     '> HCI Event: 0x0e plen 10\n  01 09 10 00\n'
                     '> HCI Event: 0xff plen 6\n  8A 6A 6A 9E A2 88\n'):
            with self.assertRaises(RuntimeError):
                bt.completion(text, 0x1009)
        with self.assertRaisesRegex(RuntimeError, 'status 0x0c'):
            bt.completion('> HCI Event: 0x0e plen 4\n  01 01 FC 0C\n', 0xfc01)

    def test_broadcom_cypress_manufacturer_gate(self):
        for manufacturer in (15, 305, 2, 65535):
            version = bytes.fromhex('09 34 12 09') + manufacturer.to_bytes(2, 'little') + bytes(2)
            self.assertEqual(bt.supports_broadcom_address_write(version), manufacturer in (15, 305))
        self.assertFalse(bt.supports_broadcom_address_write(bytes(7)))

    def test_nonzero_status_is_failure_even_with_tool_success(self):
        with self.assertRaisesRegex(RuntimeError, 'status 0x0c'):
            bt.completion('< HCI Event: 0x0e plen 4\n  01 01 FC 0C\n', 0xfc01)

    def test_wrong_opcode_and_no_event_rejected(self):
        for text in ('', '< HCI Event: 0x0e plen 4\n  01 09 10 00\n'):
            with self.assertRaisesRegex(RuntimeError, 'No matching'):
                bt.completion(text, 0xfc01)

    def test_command_status_is_not_complete(self):
        with self.assertRaises(RuntimeError):
            bt.completion('< HCI Event: 0x0f plen 4\n  00 01 01 FC\n', 0xfc01)

    def test_broadcom_version_layout(self):
        data = bt.completion('< HCI Event: 0x0e plen 12\n  01 01 10 00 09 34 12 09 0F 00 78 56\n', 0x1001)
        self.assertEqual(len(data), 8)
        self.assertEqual(int.from_bytes(data[4:6], 'little'), 15)

    def test_mapping_matches_ds4_axes(self):
        tree = ET.parse(ROOT / 'payload/ps4-smoke.xml')
        mapping = {b.attrib['label']: b.attrib['id'] for b in tree.findall('.//button')}
        self.assertEqual(mapping['PS'], 'abs_axis_2')
        self.assertEqual([mapping[n] for n in ('Up', 'Right', 'Down', 'Left')],
                         ['abs_axis_3', 'abs_axis_4', 'abs_axis_5', 'abs_axis_6'])


if __name__ == '__main__':
    unittest.main()
