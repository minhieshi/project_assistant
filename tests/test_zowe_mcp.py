from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_assistant.zowe_readonly import ZoweReadError, ZoweRunner


class ZoweRunnerTests(unittest.TestCase):
    def test_list_datasets_uses_fixed_read_only_command(self):
        runner = ZoweRunner(executable="/usr/local/bin/zowe", timeout_seconds=30, max_output_chars=5000)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ABC.TEST\nABC.TEST.CNTL\n", stderr="")
        with patch("project_assistant.zowe_readonly.Path.is_file", return_value=True), patch(
            "project_assistant.zowe_readonly.Path.resolve", return_value=Path("/usr/local/bin/zowe")
        ), patch("project_assistant.zowe_readonly.subprocess.run", return_value=completed) as run:
            text = runner.list_datasets("ABC.TEST.*", max_results=12, attributes=True)
        self.assertIn("ABC.TEST.CNTL", text)
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            ["/usr/local/bin/zowe", "zos-files", "list", "data-set", "ABC.TEST.*", "--max", "12", "--attributes"],
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_read_dataset_rejects_wildcard(self):
        runner = ZoweRunner(executable="zowe")
        with self.assertRaises(ZoweReadError):
            runner.read_dataset("ABC.*")

    def test_job_status_rejects_shell_like_input(self):
        runner = ZoweRunner(executable="zowe")
        with self.assertRaises(ZoweReadError):
            runner.get_job_status("JOB123;rm")

    def test_nonzero_exit_is_bounded_and_redacted(self):
        runner = ZoweRunner(executable="/usr/local/bin/zowe", timeout_seconds=30, max_output_chars=5000)
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="password=secret token-value=abc failure"
        )
        with patch("project_assistant.zowe_readonly.Path.is_file", return_value=True), patch(
            "project_assistant.zowe_readonly.Path.resolve", return_value=Path("/usr/local/bin/zowe")
        ), patch("project_assistant.zowe_readonly.subprocess.run", return_value=completed):
            with self.assertRaises(ZoweReadError) as ctx:
                runner.info()
        message = str(ctx.exception)
        self.assertNotIn("secret", message)
        self.assertNotIn("abc", message)
        self.assertIn("<redacted>", message)

    def test_output_is_truncated(self):
        runner = ZoweRunner(executable="/usr/local/bin/zowe", timeout_seconds=30, max_output_chars=1000)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="x" * 1500, stderr="")
        with patch("project_assistant.zowe_readonly.Path.is_file", return_value=True), patch(
            "project_assistant.zowe_readonly.Path.resolve", return_value=Path("/usr/local/bin/zowe")
        ), patch("project_assistant.zowe_readonly.subprocess.run", return_value=completed):
            output = runner.info()
        self.assertLess(len(output), 1100)
        self.assertIn("output truncated", output)


if __name__ == "__main__":
    unittest.main()
