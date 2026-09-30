"""Discover GNU screen sessions and the Claude Code processes running inside them."""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass

# A session line from `screen -ls`, e.g.
#   "\t12345.api\t(09/30/26 08:00:00)\t(Detached)"
#   "\t12345.pts-0.host\t(Attached)"
_SCREEN_LINE = re.compile(r"^\s+(?P<pid>\d+)\.(?P<name>\S+)\s+(?P<rest>.*)$")
_SCREEN_STATE = re.compile(r"\((Attached|Detached|Multi, attached|Multi, detached)\)")

_JS_RUNTIMES = {"node", "bun", "deno"}


@dataclass(frozen=True)
class ScreenSession:
    pid: int
    name: str
    state: str

    @property
    def id(self) -> str:
        return f"{self.pid}.{self.name}"


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    args: str


@dataclass(frozen=True)
class ClaudeInScreen:
    session: ScreenSession
    pid: int
    args: str
    window: str | None = None
    cwd: str | None = None
    claude_session_name: str | None = None
    claude_session_id: str | None = None

    @property
    def resume_name(self) -> str | None:
        """What to pass to `claude --resume`: the session title, else its id."""
        return self.claude_session_name or self.claude_session_id

    @property
    def attach_command(self) -> str:
        # -d -r detaches it elsewhere first, so it works for attached sessions too.
        cmd = f"screen -d -r {self.session.id}"
        if self.window is not None:
            cmd += f" -p {self.window}"
        return cmd

    def to_dict(self) -> dict:
        return {
            "session": self.session.id,
            "session_pid": self.session.pid,
            "session_name": self.session.name,
            "state": self.session.state,
            "window": self.window,
            "claude_pid": self.pid,
            "claude_args": self.args,
            "cwd": self.cwd,
            "claude_session_name": self.claude_session_name,
            "claude_session_id": self.claude_session_id,
            "resume": self.resume_name,
            "attach": self.attach_command,
        }


def _run(cmd: list[str]) -> str:
    """Run a command and return stdout, or an empty string if it cannot run."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except (FileNotFoundError, PermissionError):
        return ""
    # `screen -ls` exits non-zero even when it lists sessions, so ignore the code.
    return result.stdout


# ---------------------------------------------------------------------------
# Parsing (pure functions, easy to test)
# ---------------------------------------------------------------------------


def parse_screen_ls(output: str) -> list[ScreenSession]:
    """Parse the output of `screen -ls` into live sessions."""
    sessions = []
    for line in output.splitlines():
        match = _SCREEN_LINE.match(line)
        if not match:
            continue
        rest = match.group("rest")
        if "Dead" in rest:
            continue
        state_match = _SCREEN_STATE.search(rest)
        state = state_match.group(1) if state_match else "Unknown"
        sessions.append(
            ScreenSession(pid=int(match.group("pid")), name=match.group("name"), state=state)
        )
    return sessions


def parse_ps(output: str) -> dict[int, Process]:
    """Parse `ps -axww -o pid=,ppid=,args=` into a pid -> Process map."""
    table = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = Process(pid=pid, ppid=ppid, args=parts[2] if len(parts) > 2 else "")
    return table


def is_claude_process(args: str) -> bool:
    """Return True if a command line looks like the Claude Code CLI.

    Matches a native `claude` binary and `node .../claude-code/cli.js` style
    launches. The macOS desktop app ("Claude", capitalised) is not matched.
    """
    tokens = args.split()
    if not tokens:
        return False
    exe = os.path.basename(tokens[0])
    if exe == "claude":
        return True
    if exe in _JS_RUNTIMES and len(tokens) > 1:
        script = tokens[1]
        return os.path.basename(script) == "claude" or "claude-code" in script
    return False


def parse_window_from_environ(environ: str) -> str | None:
    """Extract the screen WINDOW variable from a NUL- or space-separated environment."""
    found = None
    for token in re.split(r"[\0\s]+", environ):
        if token.startswith("WINDOW=") and token[7:].isdigit():
            found = token[7:]
    return found


def parse_lsof_cwd(output: str) -> dict[int, str]:
    """Parse `lsof -a -d cwd -Fpn -p ...` into a pid -> cwd map."""
    cwds = {}
    pid = None
    for line in output.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def match_claude_to_sessions(
    sessions: Iterable[ScreenSession], processes: dict[int, Process]
) -> list[tuple[ScreenSession, Process]]:
    """Return (session, claude_process) pairs for every Claude process inside a screen.

    Each Claude process is attributed to its nearest screen ancestor. A Claude
    process whose ancestor is also Claude is skipped so each instance appears once.
    """
    by_pid = {s.pid: s for s in sessions}
    pairs = []
    for proc in processes.values():
        if not is_claude_process(proc.args):
            continue
        session = None
        nested = False
        seen = set()
        current = processes.get(proc.ppid)
        while current is not None and current.pid not in seen and current.pid > 1:
            seen.add(current.pid)
            if current.pid in by_pid:
                session = by_pid[current.pid]
                break
            if is_claude_process(current.args):
                nested = True
                break
            current = processes.get(current.ppid)
        if session is not None and not nested:
            pairs.append((session, proc))
    return pairs


# ---------------------------------------------------------------------------
# System access
# ---------------------------------------------------------------------------


def _window_for(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                return parse_window_from_environ(fh.read().decode(errors="replace"))
        except OSError:
            return None
    # macOS/BSD: `ps -E` appends the environment to the command line.
    return parse_window_from_environ(_run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)]))


def _cwds_for(pids: list[int]) -> dict[int, str]:
    if not pids:
        return {}
    if sys.platform.startswith("linux"):
        cwds = {}
        for pid in pids:
            with contextlib.suppress(OSError):
                cwds[pid] = os.readlink(f"/proc/{pid}/cwd")
        return cwds
    return parse_lsof_cwd(
        _run(["lsof", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(str(p) for p in pids)])
    )


def claude_config_dir() -> str:
    """Return Claude Code's config directory, honouring CLAUDE_CONFIG_DIR."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def read_claude_session_info(pid: int, config_dir: str | None = None) -> dict:
    """Read the metadata Claude Code keeps for a running process.

    Claude Code writes `<config>/sessions/<pid>.json` with the session id and,
    when the session has one, its name. Returns an empty dict if it is missing.
    """
    path = os.path.join(config_dir or claude_config_dir(), "sessions", f"{pid}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("pid") in (None, pid) else {}


def find_transcript(session_id: str, config_dir: str | None = None) -> str | None:
    """Return the path of a session's transcript, `<config>/projects/*/<id>.jsonl`."""
    root = glob.escape(config_dir or claude_config_dir())
    pattern = os.path.join(root, "projects", "*", f"{glob.escape(session_id)}.jsonl")
    matches = glob.glob(pattern)
    return max(matches, key=os.path.getmtime) if matches else None


def read_custom_title(transcript: str) -> str | None:
    """Return the latest title set with `/rename` or `claude -n` in a transcript.

    This is the name `claude --resume <name>` matches on.
    """
    title = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"custom-title"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "custom-title":
                    value = entry.get("customTitle")
                    title = value.strip() if isinstance(value, str) and value.strip() else None
    except OSError:
        return None
    return title


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def find_claude_sessions() -> list[ClaudeInScreen]:
    """Return every Claude Code instance running inside a live screen session."""
    sessions = parse_screen_ls(_run(["screen", "-ls"]))
    if not sessions:
        return []
    processes = parse_ps(_run(["ps", "-axww", "-o", "pid=,ppid=,args="]))
    pairs = match_claude_to_sessions(sessions, processes)
    cwds = _cwds_for([proc.pid for _, proc in pairs])
    results = []
    for session, proc in pairs:
        info = read_claude_session_info(proc.pid)
        session_id = _str_or_none(info.get("sessionId"))
        transcript = find_transcript(session_id) if session_id else None
        title = read_custom_title(transcript) if transcript else None
        results.append(
            ClaudeInScreen(
                session=session,
                pid=proc.pid,
                args=proc.args,
                window=_window_for(proc.pid),
                cwd=cwds.get(proc.pid) or _str_or_none(info.get("cwd")),
                claude_session_name=title,
                claude_session_id=session_id,
            )
        )
    results.sort(key=lambda r: (r.session.name, r.session.pid, r.window or "", r.pid))
    return results
