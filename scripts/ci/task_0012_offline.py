"""Offline fixtures only. Never invokes bwrap, sockets, or application code."""

import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from task_0012_verify import ipv6_routes, mountinfo, mounts


# Exact TASK-0010 /proc/net/ipv6_route inventory, including both reject rows.
REJECT = ('00000000000000000000000000000000 00 '
          '00000000000000000000000000000000 00 '
          '00000000000000000000000000000000 ffffffff 00000001 00000000 00200200       lo\n')
LOCAL = ('00000000000000000000000000000001 80 '
         '00000000000000000000000000000000 00 '
         '00000000000000000000000000000000 00000000 00000002 00000000 80200001       lo\n')
CAPTURED = REJECT + LOCAL + REJECT
MOUNTS = ('1 0 0:1 / / rw - tmpfs tmpfs rw\n'
          '2 1 8:1 /usr /usr ro,relatime - ext4 /dev/root rw\n'
          '3 1 0:2 / /proc rw,nosuid,nodev,noexec - proc proc rw\n'
          '4 1 0:3 / /dev rw,nosuid - tmpfs tmpfs rw\n'
          '5 1 0:4 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw\n')


def alias(path):
    return {'/lib': '/usr/lib', '/lib64': '/usr/lib64'}.get(path, path)


def flags(path):
    return SimpleNamespace(f_flag=os.ST_RDONLY if path in ('/usr', '/lib', '/lib64') else 0)


class Offline(unittest.TestCase):
    def test_captured_routes(self):
        self.assertEqual(ipv6_routes(CAPTURED), ['reject-default', 'local-loopback', 'reject-default'])

    def test_negative_routes(self):
        cases = {
            'real_default': REJECT.replace('00200200', '00000001'),
            'unexpected_interface': CAPTURED.replace('lo', 'eth0'),
            'external_destination_on_lo': LOCAL.replace('00000000000000000000000000000001', '20010db8000000000000000000000001'),
            'gateway': LOCAL.replace('00000000 00000002', '00000000 00000002').replace('00000000000000000000000000000000 00000000', '00000000000000000000000000000001 00000000'),
            'source_route': LOCAL.replace('00000000000000000000000000000000 00', '00000000000000000000000000000001 80'),
            'malformed_flags': LOCAL.replace('80200001', '8020000g'),
            'unknown_flags': LOCAL.replace('80200001', '80200003'),
            'truncated_field': LOCAL.replace('80200001', '80001'),
            'extra_field': LOCAL.rstrip() + ' extra\n',
            'missing_field': ' '.join(LOCAL.split()[:-1]) + '\n',
            'no_newline': LOCAL.rstrip(),
            'empty': '',
            'blank_row': CAPTURED + '\n',
            'invalid_prefix': LOCAL.replace(' 80 ', ' ff '),
            'noncanonical': LOCAL.replace(' 80 ', ' 00 '),
            'unknown_reject_destination': LOCAL.replace('80200001', '00200200'),
            'reject_with_up': REJECT.replace('00200200', '00200201'),
        }
        for name, fixture in cases.items():
            with self.subTest(name=name), self.assertRaises((AssertionError, ValueError)):
                ipv6_routes(fixture)

    def test_ubuntu_aliases_enclosing_readonly(self):
        self.assertEqual(len(mounts(MOUNTS, alias, flags)), 6)

    def test_canonicalized_separate_binds(self):
        fixture = MOUNTS + ('6 2 8:1 /usr/lib /usr/lib ro - ext4 /dev/root rw\n'
                            '7 2 8:1 /usr/lib64 /usr/lib64 ro - ext4 /dev/root rw\n')
        self.assertEqual(mounts(fixture, alias, flags)[1]['mount'], '/usr/lib')

    def test_non_usrmerge_paths(self):
        fixture = MOUNTS + ('6 1 8:1 /lib /lib ro - ext4 /dev/root rw\n'
                            '7 1 8:1 /lib64 /lib64 ro - ext4 /dev/root rw\n')
        self.assertEqual(len(mounts(fixture, lambda p: p, flags)), 6)

    def test_mount_escape_optional_fields(self):
        rows = mountinfo('1 0 8:1 / /space\\040name ro shared:1 - ext4 /dev/root rw\n')
        self.assertEqual(rows[0][0], '/space name')

    def test_negative_mounts(self):
        cases = {
            'writable_usr': MOUNTS.replace('/usr ro,', '/usr rw,'),
            'missing_usr': '\n'.join(l for l in MOUNTS.splitlines() if '/usr' not in l) + '\n',
            'missing_proc': '\n'.join(l for l in MOUNTS.splitlines() if '/proc' not in l) + '\n',
            'wrong_dev_fs': MOUNTS.replace('- tmpfs tmpfs', '- ext4 tmpfs'),
            'aliased_private': MOUNTS.replace('0:4', '0:3'),
            'wrong_private_root': MOUNTS.replace('0:4 / /tmp', '0:4 /other /tmp'),
            'duplicate': MOUNTS + MOUNTS.splitlines()[1] + '\n',
            'writable_lib_submount': MOUNTS + '6 2 8:2 / /usr/lib rw - ext4 other rw\n',
            'unknown_format': MOUNTS.replace(' - proc ', ' proc '),
            'truncated': MOUNTS.rstrip(),
            'unknown_escape': MOUNTS.replace('/usr ro', '/usr\\999 ro'),
            'ambiguous_mode': MOUNTS.replace('/usr ro,', '/usr rw,ro,'),
        }
        for name, fixture in cases.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                mounts(fixture, alias, flags)

    def test_effective_statvfs_required(self):
        with self.assertRaises(AssertionError):
            mounts(MOUNTS, alias, lambda _: SimpleNamespace(f_flag=0))
        with self.assertRaises(AssertionError):
            mounts(MOUNTS, alias, lambda _: SimpleNamespace(f_flag=os.ST_RDONLY))

    def test_unknown_alias(self):
        with self.assertRaises(AssertionError):
            mounts(MOUNTS, lambda p: '/unexpected' if p == '/lib' else p, flags)

    def test_helper_inline_syntax(self):
        source = Path(__file__).with_name('task_0012_bwrap.sh').read_text()
        parts = source.split("<<'PY'\n")[1:]
        self.assertEqual(len(parts), 2)
        verifier = Path(__file__).with_name('task_0012_verify.py').read_text()
        for i, part in enumerate(parts):
            compile((verifier if i else '') + '\n' + part.split('\nPY')[0], '<probe>', 'exec')
        self.assertEqual(source.count('20s /usr/bin/bwrap'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
