#
# This file is part of LiteX.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from unittest import mock

from litex.build.gowin import gowin


class TestGowinToolchain(unittest.TestCase):
    def test_wsl_prefers_native_gowin(self):
        def which(tool):
            return {
                "gw_sh"     : "/opt/gowin/IDE/bin/gw_sh",
                "gw_sh.exe" : "/mnt/c/Gowin/IDE/bin/gw_sh.exe",
            }.get(tool)

        with mock.patch.object(gowin, "_is_wsl", return_value=True), \
             mock.patch.object(gowin, "which", side_effect=which):
            gw_sh, gw_sh_path = gowin._find_gowin_shell()

            self.assertEqual(gw_sh, "gw_sh")
            self.assertEqual(gw_sh_path, "/opt/gowin/IDE/bin/gw_sh")
            self.assertFalse(gowin._gowin_uses_windows_paths())

    def test_wsl_falls_back_to_windows_gowin(self):
        def which(tool):
            return {
                "gw_sh"     : None,
                "gw_sh.exe" : "/mnt/c/Gowin/IDE/bin/gw_sh.exe",
            }.get(tool)

        with mock.patch.object(gowin, "_is_wsl", return_value=True), \
             mock.patch.object(gowin, "which", side_effect=which):
            gw_sh, gw_sh_path = gowin._find_gowin_shell()

            self.assertEqual(gw_sh, "gw_sh.exe")
            self.assertEqual(gw_sh_path, "/mnt/c/Gowin/IDE/bin/gw_sh.exe")
            self.assertTrue(gowin._gowin_uses_windows_paths())

    def test_wsl_windows_paths_use_wslpath_and_escape_backslashes(self):
        with mock.patch.object(gowin.subprocess, "check_output", return_value="C:\\proj\\top.v\n"):
            path = gowin._gowin_tcl_path("/mnt/c/proj/top.v", use_windows_paths=True)

        self.assertEqual(path, "C:\\\\proj\\\\top.v")

    def test_wsl_windows_paths_keep_relative_paths(self):
        with mock.patch.object(gowin.subprocess, "check_output") as check_output:
            path = gowin._gowin_tcl_path("top.v", use_windows_paths=True)

        self.assertEqual(path, "top.v")
        check_output.assert_not_called()

    def test_native_wsl_paths_are_not_rewritten(self):
        path = gowin._gowin_tcl_path("/mnt/c/proj/top.v", use_windows_paths=False)

        self.assertEqual(path, "/mnt/c/proj/top.v")


if __name__ == "__main__":
    unittest.main()
