"""Executor safety tests; no model calls, real credentials, or paid providers."""
from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.overnight import executor


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[3] / "work" / "overnight" / "executor-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "candidate"
        self.workspace.mkdir()
        self.output = self.root / "controller"

    def test_environment_is_allowlisted_and_removes_billing_credentials(self):
        source = {key: "CANARY" for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "PRIVATE_KEY",
            "OPENAI_BASE_URL", "OPENAI_FEDERATION_ID", "OPENAI_IDENTITY_TOKEN_FILE",
            "AWS_ACCESS_KEY_ID", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
            "GIT_SSH_COMMAND", "SSH_AUTH_SOCK", "HTTP_PROXY", "NODE_OPTIONS", "PYTHONPATH", "LD_PRELOAD")}
        source.update(PATH="trusted-tools", SystemRoot="os", CODEX_HOME="existing-home")
        clean = executor.clean_environment(self.root / "tmp", source)
        self.assertFalse(set(source).difference({"PATH", "SystemRoot", "CODEX_HOME"}) & set(clean))
        self.assertEqual(clean["PATH"], "trusted-tools")
        self.assertEqual(clean["CODEX_HOME"], "existing-home")

    def test_permissions_protect_control_and_hide_codex_home_from_shell_env(self):
        env = executor.clean_environment(self.output / "tmp", {"PATH": "", "CODEX_HOME": "secret-location"})
        argv = executor.permission_args(self.workspace, readonly=True, env=env)
        text = "\n".join(argv)
        self.assertIn('forced_login_method="chatgpt"', text)
        self.assertIn('permissions.overnight.network.enabled=false', text)
        self.assertIn('features.hooks=false', text)
        self.assertIn('features.plugins=false', text)
        self.assertIn('"~/.codex"="deny"', text)
        self.assertIn(json.dumps(self.workspace.as_posix()) + '="read"', text)
        self.assertIn(json.dumps((self.output / "tmp").as_posix()) + '="write"', text)
        self.assertNotIn("secret-location", text)
        self.assertNotIn("--sandbox", argv)

    def test_layout_rejects_candidate_config_and_nested_control_output(self):
        with self.assertRaises(ValueError):
            executor._layout(self.workspace, self.workspace / "outputs")
        (self.workspace / ".codex").mkdir()
        (self.workspace / ".codex" / "config.toml").write_text('[mcp_servers.bad]\ncommand="bad"')
        with self.assertRaises(ValueError):
            executor._layout(self.workspace, self.output)

    def test_read_root_cannot_grant_parent_or_filesystem(self):
        for root in (self.root, Path(self.workspace.anchor)):
            with self.subTest(root=root), self.assertRaises(ValueError):
                executor.permission_args(self.workspace, read_roots=[root])

    def test_process_stdin_and_output_are_separate_from_argv(self):
        result = executor._bounded_process([sys.executable, "-B", "-c",
            "import sys; print(sys.stdin.read()); print('err',file=sys.stderr)"], cwd=self.workspace,
            env=executor.clean_environment(self.root / "tmp"), output=self.output,
            timeout_seconds=10, stdin="SYNTHETIC PROMPT")
        self.assertTrue(result["passed"], result)
        self.assertIn("SYNTHETIC PROMPT", (self.output / "stdout.jsonl").read_text())
        self.assertIn("err", (self.output / "stderr.log").read_text())

    def test_private_metadata_never_writes_raw_output(self):
        result = executor._bounded_process([sys.executable, "-B", "-c", "print('SYNTHETIC-SECRET')"],
            cwd=self.workspace, env=executor.clean_environment(self.root / "tmp"), output=self.output,
            timeout_seconds=10, private=True)
        self.assertTrue(result["passed"], result)
        self.assertIn("SYNTHETIC-SECRET", result["_stdout"])
        self.assertFalse((self.output / "stdout.jsonl").exists())
        self.assertNotIn("SYNTHETIC-SECRET", (self.output / "process.json").read_text())

    def test_timeout_terminates_spawned_child(self):
        marker = self.root / "escaped-child.txt"
        child = "import time,pathlib;time.sleep(2);pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-B','-c'," + repr(child) + "]);time.sleep(30)"
        result = executor._bounded_process([sys.executable, "-B", "-c", parent], cwd=self.workspace,
            env=executor.clean_environment(self.root / "tmp"), output=self.output, timeout_seconds=1)
        self.assertEqual(result["error_reason"], "timeout", result)
        self.assertEqual(result["exit_code"], 124)
        time.sleep(2.2)
        self.assertFalse(marker.exists(), "child survived executor timeout")

    def test_success_also_removes_lingering_child(self):
        marker = self.root / "orphan.txt"
        child = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
        parent = "import subprocess,sys;subprocess.Popen([sys.executable,'-B','-c'," + repr(child) + "])"
        result = executor._bounded_process([sys.executable, "-B", "-c", parent], cwd=self.workspace,
            env=executor.clean_environment(self.root / "tmp"), output=self.output, timeout_seconds=10)
        self.assertTrue(result["passed"], result)
        time.sleep(1.2)
        self.assertFalse(marker.exists(), "child survived executor success")

    def test_mcp_inventory_is_masked_and_every_server_disabled(self):
        listing = "Name  Command  Args  Env  Cwd  Status  Auth\nalpha tool - SECRET=***** - enabled Unsupported\nbeta tool - - - disabled Unsupported\n"
        with patch.object(executor, "_private_cli", return_value=({"passed": True}, listing, "")) as cli:
            args = executor._mcp_disable_args("codex", self.root / "tmp")
        self.assertEqual(cli.call_args.args[1], ["mcp", "list"])
        self.assertEqual(args, ["-c", 'mcp_servers."alpha"={enabled=false,command="overnight-disabled-not-executed"}',
                                "-c", 'mcp_servers."beta"={enabled=false,command="overnight-disabled-not-executed"}'])
        with patch.object(executor, "_private_cli", return_value=({"passed": True}, "unexpected listing", "")):
            with self.assertRaises(ValueError):
                executor._mcp_disable_args("codex", self.root / "tmp")

    def test_subscription_status_rejects_api_login(self):
        for text, ready in (("Logged in using ChatGPT", True), ("Logged in using an API key: sk-...", False),
                            ("Logged in using ChatGPT\nAPI key", False)):
            with self.subTest(text=text), patch.object(executor, "_private_cli", return_value=({"passed": True}, text, "")):
                self.assertEqual(executor.authentication_status("codex", self.root / "tmp")["ready"], ready)

    def _mock_invocation(self, stdout, stderr="", exit_code=0):
        def run(command, **kwargs):
            out = kwargs["output"]
            (out / "stdout.jsonl").write_text(stdout, encoding="utf-8")
            (out / "stderr.log").write_text(stderr, encoding="utf-8")
            (out / "final.json").write_text('{"verdict":"ok"}', encoding="utf-8")
            return {"passed": exit_code == 0, "exit_code": exit_code, "elapsed_seconds": .1, "error_reason": None}
        return run

    def test_actual_exec_argv_has_subscription_only_isolation_flags(self):
        event = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}})
        schema = self.root / "trusted-schema.json"
        schema.write_text('{"type":"object"}')
        with patch.object(executor, "authentication_status", return_value={"ready": True}), \
             patch.object(executor, "sandbox_probe", return_value={"passed": True}), \
             patch.object(executor, "_mcp_disable_args", return_value=["-c", 'mcp_servers."known"={enabled=false,command="overnight-disabled-not-executed"}']), \
             patch.object(executor, "_bounded_process", side_effect=self._mock_invocation(event)) as run:
            result = executor.run_codex("codex", self.workspace, self.output, "SYNTHETIC PROMPT", 30,
                                        readonly=True, schema=schema)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["usage"], {"turns": 1, "input_tokens": 11, "output_tokens": 7})
        argv = run.call_args.args[0]
        for arg in ("exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--no-daemon"):
            self.assertIn(arg, argv)
        self.assertNotIn("SYNTHETIC PROMPT", argv)
        self.assertEqual(run.call_args.kwargs["stdin"], "SYNTHETIC PROMPT")
        self.assertEqual(argv[-1], "-")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)

    def test_failed_auth_or_probe_never_runs_model(self):
        for auth_ok in (False, True):
            output = self.root / ("failed-auth" if not auth_ok else "failed-probe")
            with patch.object(executor, "authentication_status", return_value={"ready": auth_ok}), \
                 patch.object(executor, "sandbox_probe", return_value={"passed": False}), \
                 patch.object(executor, "_bounded_process") as run:
                result = executor.run_codex("codex", self.workspace, output, "test", 30)
            self.assertFalse(result["passed"])
            run.assert_not_called()

    def test_usage_missing_or_malformed_and_quota_fail_closed(self):
        cases = [("", "", 0, "usage_unknown"),
                 (json.dumps({"type":"turn.completed", "usage":{"input_tokens": True,"output_tokens":1}}), "", 0, "usage_unknown"),
                 (json.dumps({"type":"turn.failed", "error":{"message":"usage limit reached"}}), "", 1, "quota_exhausted"),
                 ("", "429 Too many requests", 1, "rate_limit"), ("", "401 unauthorized", 1, "authentication")]
        for index, (text, err, code, expected) in enumerate(cases):
            output = self.root / str(index)
            output.mkdir()
            (output / "out").write_text(text)
            (output / "err").write_text(err)
            _, reason = executor._usage_and_error(output / "out", output / "err",
                {"passed": code == 0, "error_reason": None})
            self.assertEqual(reason, expected)

    def test_invalid_timeout_rejected_before_any_execution(self):
        for timeout in (True, 0, -1, 7201):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                executor._bounded_process(["bad"], cwd=self.workspace, env={}, output=self.output, timeout_seconds=timeout)

    def test_probe_requires_empty_workspace(self):
        (self.workspace / "keep.txt").write_text("user data")
        with self.assertRaises(ValueError):
            executor.sandbox_probe("codex", self.workspace, self.output)
        self.assertEqual((self.workspace / "keep.txt").read_text(), "user data")


if __name__ == "__main__":
    unittest.main()
