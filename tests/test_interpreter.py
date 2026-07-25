"""One interpreter-resolution policy, shared by both installers.

installer.py and install/xylem_install.py are two independent implementations
of the same infrastructure, and they resolved the Python interpreter for the
stdio servers by OPPOSITE strategies: installer.py used sys.executable, while
install/xylem_install.py tried shutil.which("python3") first. dec-013 records
why the second one is wrong on a very common Windows box -- `python3` there is
the Microsoft Store shim, while the interpreter that actually has `mcp` is
`python`, so the servers got registered into a config where they could never
start, with no diagnostic.

Two implementations of one policy means the policy can only ever be
half-fixed. These tests pin the policy to xylem_interpreter and assert that
BOTH installers go through it.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "install"))

import xylem_interpreter  # noqa: E402


class ResolvePolicy(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(xylem_interpreter.OVERRIDE_KEY, None)

    def tearDown(self):
        os.environ.pop(xylem_interpreter.OVERRIDE_KEY, None)
        if self._saved is not None:
            os.environ[xylem_interpreter.OVERRIDE_KEY] = self._saved

    def test_defaults_to_sys_executable(self):
        # The dec-013 fix, asserted directly: NOT shutil.which("python3").
        self.assertEqual(xylem_interpreter.resolve_python(), sys.executable)

    def test_never_returns_a_bare_name_by_default(self):
        # A bare "python3" in a launch command is what dec-013 is about; the
        # resolver must hand back a real path.
        resolved = xylem_interpreter.resolve_python()
        self.assertFalse(xylem_interpreter.needs_resolution(resolved))
        self.assertTrue(os.path.isabs(resolved), resolved)

    def test_environment_override_wins(self):
        os.environ[xylem_interpreter.OVERRIDE_KEY] = "/opt/venv/bin/python"
        self.assertEqual(xylem_interpreter.resolve_python(), "/opt/venv/bin/python")

    def test_get_callable_override_wins_over_environment(self):
        # install/xylem_install.py can source the override from the untracked
        # xylem.config.json, which must beat the ambient environment.
        os.environ[xylem_interpreter.OVERRIDE_KEY] = "/from/env/python"
        got = xylem_interpreter.resolve_python(
            lambda key: "/from/config/python" if key == xylem_interpreter.OVERRIDE_KEY else None
        )
        self.assertEqual(got, "/from/config/python")

    def test_empty_config_value_falls_through_to_environment(self):
        os.environ[xylem_interpreter.OVERRIDE_KEY] = "/from/env/python"
        self.assertEqual(xylem_interpreter.resolve_python(lambda key: ""), "/from/env/python")

    def test_a_raising_get_does_not_break_resolution(self):
        def boom(_key):
            raise RuntimeError("config file is corrupt")

        self.assertEqual(xylem_interpreter.resolve_python(boom), sys.executable)


class NeedsResolution(unittest.TestCase):
    def test_placeholders_and_bare_names_need_resolution(self):
        for spelling in ("$PYTHON", "${PYTHON}", "python", "python3", "  python3  "):
            self.assertTrue(xylem_interpreter.needs_resolution(spelling), spelling)

    def test_real_paths_and_other_commands_are_left_alone(self):
        for spelling in ("/usr/bin/python3.12", "C:/venv/Scripts/python.exe", "node", "", None):
            self.assertFalse(xylem_interpreter.needs_resolution(spelling), spelling)


class BothInstallersUseTheSharedPolicy(unittest.TestCase):
    """The point of the module: neither installer may keep a private policy.

    Asserted behaviourally rather than by grepping source, so that a change to
    the shared policy has to show up in both installers' output.
    """

    SENTINEL = "/sentinel/interpreter/python"

    def _patched(self):
        real = xylem_interpreter.resolve_python
        xylem_interpreter.resolve_python = lambda *a, **k: self.SENTINEL
        self.addCleanup(setattr, xylem_interpreter, "resolve_python", real)

    def test_installer_py_delegates(self):
        import installer

        self._patched()
        # installer.py adds only forward-slash normalisation for the JSON config.
        self.assertEqual(installer.resolve_python(), self.SENTINEL)

    def test_xylem_install_delegates(self):
        import xylem_install

        self._patched()
        self.assertEqual(xylem_install.resolve_python(lambda _k: None), self.SENTINEL)

    def test_the_two_installers_agree_on_the_interpreter(self):
        import installer
        import xylem_install

        self.assertEqual(
            xylem_install.resolve_python(lambda _k: None).replace("\\", "/"),
            installer.resolve_python(),
        )
        self.assertEqual(
            installer.resolve_python(),
            xylem_interpreter.resolve_python().replace("\\", "/"),
        )


if __name__ == "__main__":
    unittest.main()
