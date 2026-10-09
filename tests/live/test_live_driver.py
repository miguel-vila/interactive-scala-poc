import unittest
from tests.support import LIVE_SKILL, load

core = load(LIVE_SKILL, "live_driver")


class LiveDriverContracts(unittest.TestCase):
    def test_fs2_detection_in_exported_classpath(self):
        self.assertEqual(core.fs2_core("/tmp/fs2-core_3-3.11.0.jar"), {"present": True, "version": "3.11.0"})
        self.assertFalse(core.fs2_core("/tmp/fs2-io_3-3.11.0.jar")["present"])

    def test_multiple_fs2_versions_are_refused(self):
        with self.assertRaisesRegex(core.ContractError, 'Multiple fs2-core versions'):
            core.fs2_core('/tmp/fs2-core_3-3.10.2.jar:/tmp/fs2-core_3-3.11.0.jar')

    def test_live_driver_effect_instances_and_line_offset(self):
        for major in (2, 3):
            revision = {"catsEffect": {"present": True, "major": major},
                        "fs2": {"present": True, "version": "3.0.0"}}
            snippet = "val n = 1\nn + 2"
            refused, line = core.live_driver_source(snippet, revision, False)
            allowed, _ = core.live_driver_source(snippet, revision, True)
            self.assertEqual(refused.splitlines()[line - 1:line + 1], snippet.splitlines())
            self.assertIn("throw new Refused", refused)
            self.assertIn("Runner[Stream[IO, A]]", refused)
            self.assertIn(".unsafeRunSync()", allowed)
            self.assertIn("ContextShift" if major == 2 else "unsafe.implicits.global", allowed)
            self.assertEqual(core.compiler_lines(f"Live.scala:{line + 1}: error", line, 2), [2])
        plain, _ = core.live_driver_source("1", {"catsEffect": {"present": False}, "fs2": {"present": False}})
        self.assertNotIn("import cats.effect.IO", plain)
        self.assertNotIn("Runner[Stream", plain)
