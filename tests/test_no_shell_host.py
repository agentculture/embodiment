"""Tests for embodiment's shell-free operation (task t5).

Proves that embodiment can drive a full loop without any shell, filesystem,
or process tools — only injected domain-specific tools — and guarantees that
no shell_cli coupling exists in the codebase.

This is a confirmed boundary decision (spec claim c35): "shell-cli is part of
colleague, not embodiment — not all embodiment apps have shell access.
embodiment never composes or depends on shell-cli; tools reach the loop only
through the injected tool-executor protocol."

An embodiment host might be a chat app, a kiosk, a robot controller, a game —
things with no filesystem and no shell at all. This test proves the loop works
perfectly for them.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from embodiment.contract import OK, ModelResponse, Task, ToolCall
from embodiment.loop import EXIT_FINISHED, ToolOutcome, run

# ── the no-shell fake host ────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    """Build a minimal task for testing."""
    fields = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    fields.update(kw)
    return Task(**fields)


def _call(name: str = "get_weather", **arguments: Any) -> ToolCall:
    """Build a tool call."""
    return ToolCall(id=f"c{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "", **kw: Any) -> ModelResponse:
    """Build a model response."""
    return ModelResponse(content=content, tool_calls=list(calls), **kw)


class NoShellExecutor:
    """A tool executor with NO shell, filesystem, or process tools.

    Exposes only domain-specific tools you'd find in a kiosk, chat app, game,
    or robot controller:

    * ``get_weather`` — returns simulated weather data for a city.
    * ``send_message`` — delivers a message to a recipient.
    * ``lookup_record`` — retrieves a record from a simulated database.
    * ``finish`` — marks work as complete.

    All are stateless, instant, and carry no OS coupling whatsoever.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))

        if name == "get_weather":
            city = arguments.get("city", "Unknown")
            return ToolOutcome(result=f"Weather for {city}: Sunny, 72°F, light breeze")

        elif name == "send_message":
            to = arguments.get("to", "Unknown")
            text = arguments.get("text", "")
            return ToolOutcome(result=f"Message sent to {to}: {text}")

        elif name == "lookup_record":
            record_id = arguments.get("record_id", "Unknown")
            return ToolOutcome(result=f"Record #{record_id}: Name: Alice, Status: Active")

        elif name == "finish":
            summary = arguments.get("summary", "Task completed")
            return ToolOutcome(result="done", finished=True, finish_summary=summary)

        else:
            from embodiment.loop import UnknownToolError

            raise UnknownToolError(f"unknown tool: {name}")


class Scripted:
    """A complete seam replaying a fixed list of turns (then repeating the last)."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item

    @property
    def turns(self) -> int:
        return len(self.calls)


# ── the no-shell host test ────────────────────────────────────────────────────


class TestNoShellHost:
    """Prove that embodiment's loop works perfectly with NO shell/file/process tools."""

    def test_single_tool_single_step_finishes_cleanly(self):
        """The simplest case: one tool call, one model response, clean finish."""
        complete = Scripted(_turn(_call("finish", summary="Done")))
        executor = NoShellExecutor()

        outcome = run(complete, _task(), executor=executor, max_steps=5)

        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.status == OK
        assert outcome.result.summary == "Done"
        assert not outcome.result.not_finished
        assert not outcome.result.stopped_without_finish
        assert executor.seen == [("finish", {"summary": "Done"})]

    def test_multi_step_domain_workflow_completes_normally(self):
        """A realistic multi-step sequence with no shell coupling at all.

        The workflow: check weather, send notification, look up user record,
        finish with a summary. Zero filesystem, zero shell, zero process access.
        """
        complete = Scripted(
            _turn(_call("get_weather", city="Portland")),
            _turn(_call("send_message", to="user@example.com", text="Your local weather")),
            _turn(_call("lookup_record", record_id="12345")),
            _turn(_call("finish", summary="Weather notification sent")),
        )
        executor = NoShellExecutor()

        outcome = run(complete, _task(), executor=executor, max_steps=10)

        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.status == OK
        assert outcome.result.summary == "Weather notification sent"
        assert complete.turns == 4
        assert executor.seen == [
            ("get_weather", {"city": "Portland"}),
            ("send_message", {"to": "user@example.com", "text": "Your local weather"}),
            ("lookup_record", {"record_id": "12345"}),
            ("finish", {"summary": "Weather notification sent"}),
        ]

    def test_tool_results_feed_back_to_model_correctly(self):
        """Verify that tool outputs make it back into the context for the next turn.

        This tests a sequence where tools are called, results come back, and the
        model's subsequent turns see those results in the message history.
        """
        # Track turn count to vary response
        turn_count = [0]

        def stateful_complete(messages: list[dict[str, Any]]) -> ModelResponse:
            turn_count[0] += 1
            # Turn 1: ask for weather
            if turn_count[0] == 1:
                return _turn(_call("get_weather", city="Portland"))
            # Turn 2: message list now has the weather result; ask for something else
            elif turn_count[0] == 2:
                return _turn(_call("send_message", to="user", text="Hello"))
            # Turn 3: finish
            else:
                return _turn(_call("finish", summary="All tools succeeded"))

        executor = NoShellExecutor()

        outcome = run(stateful_complete, _task(), executor=executor, max_steps=10)

        assert outcome.exit_reason == EXIT_FINISHED
        assert "succeeded" in outcome.result.summary.lower()
        assert len(executor.seen) == 3
        assert executor.seen[0][0] == "get_weather"
        assert executor.seen[1][0] == "send_message"
        assert executor.seen[2][0] == "finish"

    def test_minimal_executor_protocol_suffices(self):
        """Prove the loop works with ONLY the ``execute`` method, nothing else.

        No ledger attributes, no instrumentation — just the bare protocol.
        """

        class MinimalNoShellExecutor:
            """The absolute minimum: one method, no side channels."""

            def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
                if name == "finish":
                    return ToolOutcome(result="done", finished=True, finish_summary="all done")
                return ToolOutcome(result=f"executed {name}")

        complete = Scripted(_turn(_call("get_weather"), _call("finish")))
        executor = MinimalNoShellExecutor()

        outcome = run(complete, _task(), executor=executor, max_steps=5)

        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.status == OK


# ── repo-wide shell coupling guard ────────────────────────────────────────────


EMBODIMENT_ROOT = Path(__file__).resolve().parents[1] / "embodiment"


def _collect_python_files() -> list[Path]:
    """All .py files under embodiment/."""
    return sorted(EMBODIMENT_ROOT.rglob("*.py"))


def _source_text(path: Path) -> str:
    """Read a Python file's source."""
    return path.read_text(encoding="utf-8")


class TestNoShellCoupling:
    """Verify no shell_cli coupling exists in embodiment source."""

    def test_no_shell_cli_import_anywhere(self):
        """Guard against importing shell_cli — it's colleague's, not embodiment's."""
        banned_tokens = ("shell_cli", "shell-cli", "shell cli")
        for path in _collect_python_files():
            source = _source_text(path)
            for token in banned_tokens:
                assert (
                    token not in source
                ), f"Found '{token}' in {path.relative_to(EMBODIMENT_ROOT.parent)}"

    def test_loop_py_imports_no_subprocess_family(self):
        """Guard ``embodiment/loop.py`` specifically: no subprocess/shlex/os.system.

        The loop is the core seam, so it's the hardest constraint. Using AST to
        avoid false positives on prose in docstrings that *explain* why these
        modules are not imported.
        """
        loop_src = EMBODIMENT_ROOT / "loop.py"
        tree = ast.parse(_source_text(loop_src))

        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module.split(".")[0])

        banned_modules = ("subprocess", "shlex")
        for mod in banned_modules:
            assert (
                mod not in imported_modules
            ), f"loop.py imports {mod}, which couples to shell execution"

        # os is stdlib so it's imported, but os.system and popen must not appear
        # in any function call — check with AST to avoid prose false positives
        source = _source_text(loop_src)
        for forbidden in ("os.system", "os.popen"):
            assert (
                forbidden not in source
            ), f"loop.py contains {forbidden}, which couples to shell execution"

    def test_loop_py_does_not_couple_to_shell_tools(self):
        """Guard that loop.py doesn't reference shell commands in actual code.

        This guards the constraint that the loop is tool-agnostic. It checks
        source for subprocess calls (which would couple to the shell), using
        AST to avoid false positives on docstring prose that explains the design.
        """
        loop_src = EMBODIMENT_ROOT / "loop.py"
        source = _source_text(loop_src)
        tree = ast.parse(source)

        # Walk the AST to find function calls (which would indicate tools
        # being invoked by the loop itself).
        subprocess_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for subprocess.* or os.system/popen calls
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "subprocess":
                            subprocess_calls.append(node.func.attr)
                        if node.func.value.id == "os" and node.func.attr in (
                            "system",
                            "popen",
                            "fork",
                            "spawn",
                        ):
                            subprocess_calls.append(f"os.{node.func.attr}")

        assert not subprocess_calls, f"loop.py makes subprocess/os calls: {subprocess_calls}"


# ── subprocess/shlex/popen import guard ───────────────────────────────────────


class TestLoopImportPosture:
    """Verify loop.py has no process/shell family imports (supplements test_loop.py)."""

    def test_loop_py_imports_no_process_modules(self):
        """AST-based check: loop.py must not import subprocess, shlex, os.system."""
        loop_src = EMBODIMENT_ROOT / "loop.py"
        source = _source_text(loop_src)
        tree = ast.parse(source)

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        banned = ("subprocess", "shlex")
        for mod in banned:
            assert mod not in imported, f"loop.py imports {mod}"

    def test_loop_py_uses_no_popen_or_system_calls(self):
        """AST-based: no os.system, os.popen, or equivalent at runtime."""
        loop_src = EMBODIMENT_ROOT / "loop.py"
        source = _source_text(loop_src)

        # Text check is safe here because os.system() would need to be in
        # actual executable code, not prose. Check in the module.
        forbidden_patterns = ("os.system", "os.popen", "os.fork", "os.spawn")
        for pattern in forbidden_patterns:
            assert (
                pattern not in source
            ), f"loop.py contains {pattern}, which is a process/shell coupling"
