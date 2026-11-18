"""Exercise a fresh addon import with dependencies missing from the frozen launcher."""
from pathlib import Path
import subprocess
import sys
import unittest


class LauncherCompatibilityTests(unittest.TestCase):
    def test_addon_loads_without_uuid(self):
        script = (
            "import sys; "
            "sys.modules['uuid'] = None; "
            "from ui_support import client, core; "
            "assert client.BUTTON_TEXT == 'Sheikah Mods'; "
            "assert core.VERSION == '1.2.2'; "
            "print('Standalone addon loaded without uuid')"
        )
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('loaded without uuid', result.stdout)


if __name__ == '__main__':
    unittest.main()
