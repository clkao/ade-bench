# ABOUTME: Tests for AbstractInstalledAgent, focusing on timeout output salvage.
# ABOUTME: Validates _copy_log_file_from_container behavior.

from unittest.mock import MagicMock
from pathlib import Path

from ade_bench.agents.agent_name import AgentName
from ade_bench.agents.installed_agents.abstract_installed_agent import AbstractInstalledAgent
from ade_bench.harness_models import TerminalCommand


class ConcreteInstalledAgent(AbstractInstalledAgent):
    """Concrete subclass for testing AbstractInstalledAgent."""
    NAME = AgentName.CLAUDE_CODE

    @property
    def _env(self) -> dict[str, str]:
        return {"TEST_KEY": "test_value"}

    @property
    def _install_agent_script(self) -> Path:
        return Path("/fake/install.sh")

    def _run_agent_commands(self, task_prompt: str) -> list[TerminalCommand]:
        return []


class TestCopyLogFileFromContainer:
    def test_copies_log_file_successfully(self, tmp_path):
        """Reads /tmp/agent_output.log from container and writes to logging_dir."""
        agent = ConcreteInstalledAgent()
        container = MagicMock()
        container.exec_run.return_value = MagicMock(
            exit_code=0, output=b"some agent output\nwith multiple lines"
        )

        result = agent._copy_log_file_from_container(container, tmp_path)

        container.exec_run.assert_called_once_with(["cat", "/tmp/agent_output.log"])
        assert result == "some agent output\nwith multiple lines"
        assert (tmp_path / "agent_output.log").read_text() == "some agent output\nwith multiple lines"

    def test_returns_empty_string_on_nonzero_exit(self, tmp_path):
        """Returns empty string when cat command fails (file not found)."""
        agent = ConcreteInstalledAgent()
        container = MagicMock()
        container.exec_run.return_value = MagicMock(
            exit_code=1, output=b"No such file or directory"
        )

        result = agent._copy_log_file_from_container(container, tmp_path)

        assert result == ""
        assert not (tmp_path / "agent_output.log").exists()

    def test_returns_empty_string_on_exception(self, tmp_path):
        """Returns empty string when container exec raises an exception."""
        agent = ConcreteInstalledAgent()
        container = MagicMock()
        container.exec_run.side_effect = Exception("Container is dead")

        result = agent._copy_log_file_from_container(container, tmp_path)

        assert result == ""
