"""Installer selection tests; no downloads, subprocesses or environment changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import subprocess

from setup_mvp import choose_torch_build, installation_commands, ROOT, main


class SetupTests(unittest.TestCase):
    def test_default_profile_and_persisted_cu128(self):
        with tempfile.TemporaryDirectory() as directory, patch('setup_mvp.sys.platform','win32'):
            profile = Path(directory)/'profile'
            self.assertEqual(choose_torch_build(profile), 'default')
            profile.write_text('cu128\n')
            self.assertEqual(choose_torch_build(profile), 'cu128')
            self.assertEqual(choose_torch_build(profile, 'default'), 'default')
            profile.write_text('invalid')
            with self.assertRaises(ValueError): choose_torch_build(profile)

    def test_failed_install_keeps_explicit_build_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root/'.venv/bin/python'
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch('setup_mvp.ROOT', root), patch('setup_mvp.sys.platform','win32'), \
                 patch('setup_mvp.sys.version_info', (3,12)), \
                 patch('setup_mvp.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'fake pip')):
                with self.assertRaises(subprocess.CalledProcessError): main(['--torch-build','cu128'])
            self.assertEqual((root/'.venv/.torch-build').read_text().strip(), 'cu128')

    def test_cuda_profile_rejected_on_mac(self):
        with tempfile.TemporaryDirectory() as directory, patch('setup_mvp.sys.platform','darwin'):
            with self.assertRaises(ValueError): choose_torch_build(Path(directory)/'profile', 'cu128')

    def test_cuda_dependencies_cannot_reinstall_default_torch(self):
        commands = installation_commands(Path('/fake/python'), 'cu128')
        self.assertEqual([Path(c[-1]).name for c in commands], ['requirements-cu128.txt','requirements-runtime.txt'])
        runtime = (ROOT/'requirements-runtime.txt').read_text()
        self.assertNotIn('torch==', runtime)
        self.assertNotIn('torchvision==', runtime)
        self.assertEqual(Path(installation_commands(Path('/fake/python'), 'default')[0][-1]).name, 'requirements.txt')


if __name__ == '__main__': unittest.main()
