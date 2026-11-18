from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from runpy import run_path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


LOGGER = Path(__file__).resolve().parents[3] / 'Logger.py'


class RuntimeLoggingTests(unittest.TestCase):
    def setUp(self):
        self.namespace = run_path(str(LOGGER))
        self.log = self.namespace['log']
        self.run_native = self.namespace['run_native_command']
        self.callbacks = {}
        self.removed_callback = None
        om = SimpleNamespace(
            MCommandMessage=SimpleNamespace(
                kInfo=2, addCommandOutputFilterCallback=self.register,
            ),
            MMessage=SimpleNamespace(removeCallback=self.remove),
        )
        maya_api = SimpleNamespace(OpenMaya=om)
        modules = patch.dict(sys.modules, {'maya': SimpleNamespace(api=maya_api),
                                           'maya.api': maya_api})
        modules.start()
        self.addCleanup(modules.stop)

    def register(self, callback):
        self.callbacks[7] = callback
        return 7

    def remove(self, callback_id):
        self.removed_callback = self.callbacks.pop(callback_id)

    def test_quiet_default_and_live_detail_switch(self):
        def messages():
            self.log.info('progress')
            self.log.ok('completed')
            self.log.warn('warning')
            self.log.error('error')

        output = StringIO()
        with redirect_stdout(output):
            messages()
        self.assertEqual(output.getvalue().splitlines(), ['[WARN] warning', '[ERR] error'])

        self.log.set_detailed(True)
        output = StringIO()
        with redirect_stdout(output):
            messages()
        self.assertIn('[INFO] progress', output.getvalue())
        self.assertIn('[OK] completed', output.getvalue())
        self.log.set_detailed(False)
        self.assertFalse(self.log.details_enabled())

    def test_quiet_native_filter_leaves_warnings_and_other_info_visible(self):
        def command(value):
            callback = self.callbacks[7]
            for prefix in ('Query', 'Strict', 'Project'):
                self.assertTrue(callback('  [WCT Native {}] runtime'.format(prefix), 2, None))
            self.assertFalse(callback('[WCT Native Query] warning', 3, None))
            self.assertFalse(callback('[WCT Native Query] error', 4, None))
            self.assertFalse(callback('Other tool info', 2, None))
            return value

        result = object()
        self.assertIs(self.run_native(command, result), result)
        self.assertFalse(self.callbacks)
        self.assertFalse(self.removed_callback('[WCT Native Query] runtime', 2, None))

    def test_native_cleanup_on_failure_and_bypass_in_detail_mode(self):
        def fail():
            self.assertIn(7, self.callbacks)
            raise RuntimeError('command failed')

        with self.assertRaisesRegex(RuntimeError, 'command failed'):
            self.run_native(fail)
        self.assertFalse(self.callbacks)
        self.removed_callback = None
        self.log.set_detailed(True)
        self.assertEqual(self.run_native(lambda: 42), 42)
        self.assertIsNone(self.removed_callback)
