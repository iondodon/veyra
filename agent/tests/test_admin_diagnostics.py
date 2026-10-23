import unittest
from unittest.mock import patch, Mock
from admin_diagnostics import AdminDiagnostics


class AdminTests(unittest.TestCase):
    def test_rejects_arbitrary_commands_and_passwords(self):
        admin = AdminDiagnostics()
        for payload in ({}, {'command': 'id'}, {'action': 'firewall', 'password': 'not-real'}, []):
            self.assertEqual(admin.start(payload)[0], 400)

    def test_duplicate(self):
        admin = AdminDiagnostics()
        admin.result['running'] = True
        self.assertEqual(admin.start({'action': 'firewall'})[0], 409)

    @patch('admin_diagnostics.subprocess.Popen')
    @patch('admin_diagnostics.Path.is_file', return_value=True)
    def test_fixed_read_only_command(self, exists, popen):
        proc = Mock(returncode=0)
        proc.communicate.return_value = ('table inet filter {}', '')
        popen.return_value = proc
        admin = AdminDiagnostics()
        admin._run()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ['/usr/bin/pkexec', '--disable-internal-agent', '/usr/sbin/nft', 'list', 'ruleset'])
        self.assertNotIn('shell', kwargs)
        self.assertEqual(admin.snapshot()['output'], 'table inet filter {}')
        self.assertFalse(admin.snapshot()['running'])

    @patch('admin_diagnostics.subprocess.Popen')
    @patch('admin_diagnostics.Path.is_file', return_value=True)
    def test_auth_cancelled(self, exists, popen):
        proc = Mock(returncode=126)
        proc.communicate.return_value = ('', 'Not authorized')
        popen.return_value = proc
        admin = AdminDiagnostics(); admin._run()
        self.assertIn('cancelled', admin.snapshot()['message'])


if __name__ == '__main__':
    unittest.main()
