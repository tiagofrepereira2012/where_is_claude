# where-is-claude

Running several Claude Code instances in GNU `screen`? `where-is-claude` tells you
which screen session, and which window inside it, each one lives in.

```console
$ where-is-claude
SESSION         STATE     WINDOW  PID    DIRECTORY                ATTACH WITH
71234.api       Detached  0       71236  ~/code/api               screen -d -r 71234.api -p 0
71300.frontend  Attached  2       71302  ~/code/frontend          screen -d -r 71300.frontend -p 2
```

Copy the command from the last column to jump straight to that Claude.

## Install

```console
pipx install where-is-claude
# or
uv tool install where-is-claude
```

## Usage

```console
where-is-claude           # table of screen sessions running Claude
where-is-claude --json    # the same, as JSON
where-is-claude --version
```

## How it works

1. `screen -ls` lists the live sessions and the PID of each screen server.
2. `ps` gives the process tree. Every `claude` process is attributed to its
   nearest screen ancestor.
3. The screen window number comes from the process's `WINDOW` environment
   variable, and the directory comes from its working directory.

It works on macOS and Linux, and has no dependencies beyond the Python standard
library. The macOS Claude desktop app is not counted, only the Claude Code CLI.

## Development

```console
uv sync
uv run ruff check .          # lint
uv run ruff format .         # format
uv run ty check              # type check
uv run pytest                # tests
uv run where-is-claude
```

## Publishing to PyPI

```console
uv build
uv publish        # needs a PyPI API token in UV_PUBLISH_TOKEN
```

Bump `__version__` in `src/where_is_claude/__init__.py` before each release.

## License

MIT
