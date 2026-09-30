from where_is_claude.core import (
    ClaudeInScreen,
    ScreenSession,
    is_claude_process,
    match_claude_to_sessions,
    parse_lsof_cwd,
    parse_ps,
    parse_screen_ls,
    parse_window_from_environ,
)

SCREEN_LS = """\
There are screens on:
\t71234.api\t(09/30/26 08:00:00)\t(Detached)
\t71300.frontend\t(Attached)
\t71400.old\t(Dead ???)
\t71500.pts-0.host\t(Multi, detached)
4 Sockets in /var/folders/xx/T/.screen.
"""

PS = """\
    1     0 /sbin/launchd
71234     1 SCREEN -S api
71235 71234 -zsh
71236 71235 claude --resume api work
71237 71236 /bin/zsh -c ls
71238 71236 claude mcp serve
71300     1 SCREEN -S frontend
71301 71300 -zsh
71302 71301 node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js
71500     1 SCREEN -S host
71501 71500 -bash
80000     1 /Applications/Claude.app/Contents/MacOS/Claude
80001   955 claude
"""


def test_parse_screen_ls_skips_dead_and_reads_state():
    sessions = parse_screen_ls(SCREEN_LS)
    assert [s.id for s in sessions] == ["71234.api", "71300.frontend", "71500.pts-0.host"]
    assert [s.state for s in sessions] == ["Detached", "Attached", "Multi, detached"]


def test_parse_screen_ls_no_sockets():
    assert parse_screen_ls("No Sockets found in /tmp/.screen.\n") == []


def test_is_claude_process():
    assert is_claude_process("claude")
    assert is_claude_process("/Users/me/.local/bin/claude --resume x")
    assert is_claude_process("node /opt/lib/node_modules/@anthropic-ai/claude-code/cli.js")
    assert not is_claude_process("/Applications/Claude.app/Contents/MacOS/Claude")
    assert not is_claude_process("where-is-claude")
    assert not is_claude_process("python -m where_is_claude")
    assert not is_claude_process("vim claude.md")
    assert not is_claude_process("")


def test_match_claude_to_sessions():
    sessions = parse_screen_ls(SCREEN_LS)
    pairs = match_claude_to_sessions(sessions, parse_ps(PS))
    found = sorted((s.name, p.pid) for s, p in pairs)
    # 71238 is a child of another claude, 80001 is outside screen.
    assert found == [("api", 71236), ("frontend", 71302)]


def test_parse_window_from_environ():
    assert parse_window_from_environ("HOME=/x\0STY=1.api\0WINDOW=3\0") == "3"
    assert parse_window_from_environ("claude --resume PWD=/x WINDOW=0 TERM=screen") == "0"
    assert parse_window_from_environ("PWD=/x") is None


def test_parse_lsof_cwd():
    out = "p101\nfcwd\nn/Users/me/api\np202\nfcwd\nn/Users/me/web\n"
    assert parse_lsof_cwd(out) == {101: "/Users/me/api", 202: "/Users/me/web"}


def test_attach_command():
    session = ScreenSession(pid=1, name="api", state="Detached")
    assert ClaudeInScreen(session, 2, "claude", window="1").attach_command == (
        "screen -d -r 1.api -p 1"
    )
    assert ClaudeInScreen(session, 2, "claude").attach_command == "screen -d -r 1.api"


def test_read_claude_session_info(tmp_path):
    from where_is_claude.core import read_claude_session_info

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "4242.json").write_text(
        '{"pid": 4242, "sessionId": "abc", "name": "Fix login bug", "cwd": "/x"}'
    )
    (sessions / "5000.json").write_text("not json")
    (sessions / "6000.json").write_text('{"pid": 1234, "name": "stale"}')

    assert read_claude_session_info(4242, str(tmp_path))["name"] == "Fix login bug"
    assert read_claude_session_info(5000, str(tmp_path)) == {}
    assert read_claude_session_info(6000, str(tmp_path)) == {}
    assert read_claude_session_info(7000, str(tmp_path)) == {}


def test_read_custom_title_uses_latest(tmp_path):
    from where_is_claude.core import find_transcript, read_custom_title

    project = tmp_path / "projects" / "-Users-me-api"
    project.mkdir(parents=True)
    transcript = project / "abc-123.jsonl"
    transcript.write_text(
        '{"type":"user","message":"say \\"custom-title\\""}\n'
        '{"type":"custom-title","customTitle":"First name","sessionId":"abc-123"}\n'
        "broken line\n"
        '{"type":"custom-title","customTitle":"Fix login bug","sessionId":"abc-123"}\n'
    )
    found = find_transcript("abc-123", str(tmp_path))
    assert found == str(transcript)
    assert read_custom_title(found) == "Fix login bug"
    assert find_transcript("missing", str(tmp_path)) is None


def test_read_custom_title_none_when_untitled(tmp_path):
    from where_is_claude.core import read_custom_title

    transcript = tmp_path / "x.jsonl"
    transcript.write_text('{"type":"user","message":"hi"}\n')
    assert read_custom_title(str(transcript)) is None


def test_resume_name_falls_back_to_session_id():
    session = ScreenSession(pid=1, name="api", state="Detached")
    titled = ClaudeInScreen(session, 2, "claude", claude_session_name="Fix", claude_session_id="u")
    untitled = ClaudeInScreen(session, 2, "claude", claude_session_id="uuid-1")
    assert titled.resume_name == "Fix"
    assert untitled.resume_name == "uuid-1"
    assert ClaudeInScreen(session, 2, "claude").resume_name is None
