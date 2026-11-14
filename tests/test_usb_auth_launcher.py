import contextlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('usb_launcher', str(Path(__file__).resolve().parents[1] / 'payload/gimx-ps4'))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(launcher, 'Path', side_effect=self.path))
        self.stack.enter_context(patch.object(launcher.os, 'getuid', return_value=0))
        self.stack.enter_context(patch.object(launcher.os, 'umask'))
        self.stack.enter_context(patch.object(launcher, 'adapter_address', return_value='12:34:56:78:9A:BC'))
        self.stack.enter_context(patch.dict(os.environ, {'GIMX_DS4_USB':'stale', 'GIMX_DS4_USB_TRACE':'1',
                                                      'GIMX_DS4_AUTH2_TABLE':'stale', 'GIMX_DS4_KEYS':'stale'}))
        self.run = self.stack.enter_context(patch.object(launcher.subprocess, 'run'))
        self.execute = self.stack.enter_context(patch.object(launcher.os, 'execvp'))
        self.write('/var/lib/gimx/ps4.json', json.dumps({'adapter':'12:34:56:78:9A:BC','ps4':'AA:BB:CC:DD:EE:FF'}))
        self.write('/var/lib/gimx/install.ready', '')

    def path(self, value):
        value = Path(value)
        return self.root / str(value).lstrip('/') if value.is_absolute() else value

    def write(self, path, text):
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def start(self, *args):
        with patch('sys.argv', ['gimx-ps4','run','--network-only',*args]):
            launcher.main()

    def enable_modes(self):
        self.write('/var/lib/gimx/auth-modes.ready', '')
        return patch.object(launcher, 'validate_table', return_value='/private/table.bin')

    def test_old_binary_cannot_silently_ignore_local_mode(self):
        with self.assertRaisesRegex(ValueError, 'extension is not installed'):
            self.start('--usb-auth', '/dev/hidraw0', '--auth2-table', '/private/table.bin')
        self.run.assert_not_called()
        self.execute.assert_not_called()

    def test_explicit_usb_donor_and_trace(self):
        with self.enable_modes():
            self.start('--usb-auth', '/dev/hidraw0', '--auth2-table', '/private/table.bin', '--usb-auth-trace')
        self.assertEqual(os.environ['GIMX_DS4_USB'], '/dev/hidraw0')
        self.assertEqual(os.environ['GIMX_DS4_USB_TRACE'], '1')
        self.assertNotIn('GIMX_DS4_KEYS', os.environ)
        self.assertEqual(self.execute.call_args.args[1][-2:], ['--bdaddr','AA:BB:CC:DD:EE:FF'])
        self.assertNotIn('--config', self.execute.call_args.args[1])

    def test_explicit_bluetooth_mode_clears_stale_auth_environment(self):
        self.start('--auth-mode', 'bluetooth')
        for name in ('GIMX_DS4_USB', 'GIMX_DS4_USB_TRACE', 'GIMX_DS4_AUTH2_TABLE', 'GIMX_DS4_KEYS'):
            self.assertNotIn(name, os.environ)
        self.execute.assert_called_once()

    def test_software_mode_never_discovers_a_donor(self):
        self.write('/private/keys/key.pem', 'PEM is checked by C')
        self.write('/private/keys/serial.txt', '123456789abcdef0')
        self.write('/private/keys/sig.bin', 'x' * 256)
        with self.enable_modes(), patch.object(launcher, 'usb_donor', side_effect=AssertionError('must not use USB')):
            self.start('--auth-mode', 'software', '--auth-keys', '/private/keys',
                       '--auth2-table', '/private/table.bin', '--auth-trace')
        self.assertEqual(os.environ['GIMX_DS4_KEYS'], str(self.path('/private/keys').resolve()))
        self.assertNotIn('GIMX_DS4_USB', os.environ)
        self.assertEqual(os.environ['GIMX_DS4_AUTH2_TABLE'], '/private/table.bin')
        self.execute.assert_called_once()

    def test_required_files_and_mode_conflicts(self):
        cases = [('--usb-auth',), ('--auth-mode', 'software'),
                 ('--auth-mode', 'software', '--auth2-table', '/t'),
                 ('--auth-mode', 'bluetooth', '--usb-auth'),
                 ('--auth-mode', 'bluetooth', '--auth2-table', '/t'),
                 ('--auth-mode', 'software', '--usb-device', '/dev/hidraw0'),
                 ('--auth-mode', 'usb', '--auth-keys', '/k', '--auth2-table', '/t'),
                 ('--usb-auth', '--usb-device', '/dev/hidraw0')]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.start(*args)
        self.run.assert_not_called()
        self.execute.assert_not_called()

    def test_invalid_table_before_start(self):
        self.write('/var/lib/gimx/auth-modes.ready', '')
        self.write('/private/table.bin', 'wrong')
        with self.assertRaisesRegex(ValueError, '512-byte'):
            self.start('--usb-auth', '/dev/hidraw0', '--auth2-table', '/private/table.bin')
        self.write('/private/table.bin', '0' * 512)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.start('--usb-auth', '/dev/hidraw0', '--auth2-table', '/private/table.bin')
        self.run.assert_not_called()
        self.execute.assert_not_called()

    def test_table_validated_path(self):
        self.write('/private/table.bin', '0' * 512)
        with patch.object(launcher.hashlib, 'sha256') as digest:
            digest.return_value.hexdigest.return_value = '98b700c568bd40d1a3a280a63918c357b1713666d7fb992ab08e19f96ba31b7d'
            result = launcher.validate_table('/private/table.bin')
        self.assertEqual(result, str(self.path('/private/table.bin').resolve()))

    def test_keys_missing_or_malformed(self):
        with self.assertRaises(ValueError): launcher.validate_keys('/private/keys')
        self.write('/private/keys/key.pem', 'validated by backend')
        self.write('/private/keys/serial.txt', 'z' * 16)
        self.write('/private/keys/sig.bin', 'x' * 256)
        with self.assertRaisesRegex(ValueError, 'hexadecimal'): launcher.validate_keys('/private/keys')
        self.write('/private/keys/serial.txt', 'a' * 32)
        launcher.validate_keys('/private/keys')
        self.write('/private/keys/sig.bin', 'x' * 255)
        with self.assertRaisesRegex(ValueError, 'sig.bin'): launcher.validate_keys('/private/keys')

    def test_auto_discovery_rejects_ambiguous_or_bluetooth_devices(self):
        self.write('/sys/class/hidraw/hidraw0/device/uevent', 'HID_ID=0005:0000054C:000009CC\n')
        with self.assertRaisesRegex(ValueError, 'found 0'):
            launcher.usb_donor('auto')
        self.write('/sys/class/hidraw/hidraw1/device/uevent', 'HID_ID=0003:0000054C:000009CC\n')
        self.assertEqual(launcher.usb_donor('auto'), '/dev/hidraw1')
        self.write('/sys/class/hidraw/hidraw2/device/uevent', 'HID_ID=0003:0000054C:000005C4\n')
        with self.assertRaisesRegex(ValueError, 'found 2'):
            launcher.usb_donor('auto')


if __name__ == '__main__':
    unittest.main()
