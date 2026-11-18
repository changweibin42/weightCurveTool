"""Run with mayapy after building; does not need maya.standalone or a scene."""
from pathlib import Path
import sys
import unittest
from check_helpers import load_profiles

if __name__=='__main__':
    profiles=load_profiles();status=profiles.get_native_status()
    print('Native status:',status)
    if not status['enabled']:
        print('ERROR: native backend did not load. Verification must not silently test Python.')
        sys.exit(1)
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/'tests'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
