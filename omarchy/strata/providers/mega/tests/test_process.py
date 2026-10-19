from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mega_process import MAX_OUTPUT, run_cli


class ProcessTests(unittest.TestCase):
    def test_captures_small_output_and_return_code_without_stderr(self):
        result = run_cli([sys.executable, "-c", "import sys; print('metadata'); print('private diagnostic',file=sys.stderr);sys.exit(3)"])
        self.assertEqual(result.stdout, b"metadata\n")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr, b"")

    def test_overflow_is_stopped_before_full_capture(self):
        before = time.monotonic()
        with self.assertRaisesRegex(ValueError, "output-limit"):
            run_cli([sys.executable, "-c", f"import os,time;os.write(1,b'x'*{MAX_OUTPUT * 2});time.sleep(10)"])
        self.assertLess(time.monotonic() - before, 2)

    def test_timeout_kills_a_client_that_never_finishes(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cli([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)


if __name__ == "__main__":
    unittest.main()
