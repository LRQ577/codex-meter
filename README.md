# Codex Meter

[简体中文](README.zh-CN.md)

Codex Meter is an always-on-top Linux desktop widget for Codex usage and local system resources. It shows the current account's short and weekly usage windows, reset times, local task and automation counts, CPU and memory, NVIDIA GPUs, and a private local todo list.

## Requirements

- Linux desktop session with a graphical display (X11 or Wayland)
- Python 3.10 or newer, `python3-venv`, and `pip`
- Codex CLI in `PATH`, signed in on this computer, for usage and task data
- NVIDIA driver and `nvidia-smi` for GPU metrics (optional)

The quota display uses the local `codex app-server --stdio` API. The local task and automation counts depend on the Codex desktop database and may be unavailable with older or different Codex installations. These counts describe local records, not concurrently running agents or cloud tasks. Codex API changes can require a Meter update.

## Install and run

```bash
git clone https://github.com/LRQ577/codex-meter.git
cd codex-meter
./install.sh
./run.sh
```

`install.sh` creates a project-local `.venv` and installs the dependencies in `requirements.txt`. Run `./run.sh` from any working directory. If `codex` is missing from the desktop session's `PATH`, start it from a terminal with the correct `PATH` or configure your session environment.

## Desktop launcher and login autostart

```bash
./install-desktop.sh                 # Applications menu only
./install-desktop.sh --autostart     # Also start on login
./install-desktop.sh --no-autostart  # Remove login autostart
```

The launcher uses this checkout's absolute path. Run the command again after moving the project. On most Linux desktops, log out and back in if a new menu entry does not appear immediately.

## Use and data

Drag an empty part of the widget to move it. The refresh button updates all data immediately. CPU, memory, and GPU refresh every 2 seconds; Codex usage and local task counts refresh every 60 seconds. Add, complete, or remove todos in the widget. Unread Codex inbox items that need attention can appear in the todo list; recurring automations themselves are not added as personal todos.

Todos are stored only on this computer at `${XDG_DATA_HOME:-~/.local/share}/codex-meter/todos.json`. `CODEX_HOME` can override the Codex data directory; the default is `~/.codex`. The app reads the local Codex database in read-only mode and never exports login data, tokens, todos, or cache. Never commit these files to a public repository.

## Verify

```bash
./.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen timeout 10s ./run.sh
```

For the offscreen smoke test, exit code `124` means the persistent window ran until `timeout` stopped it; inspect stderr for crashes. A full desktop check should launch the Applications menu entry and confirm the widget appears. Without an NVIDIA GPU, the GPU section reports that `nvidia-smi` is unavailable.

## License

MIT; see [LICENSE](LICENSE).
