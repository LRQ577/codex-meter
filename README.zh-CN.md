# Codex Meter

[English](README.md)

Codex Meter 是 Linux 桌面置顶悬浮窗，显示当前 Codex 账号的短周期和每周额度、重置时间、本机任务与自动化数量、CPU/内存、NVIDIA GPU，以及仅保存在本机的待办清单。

## 环境要求

- 有图形桌面会话的 Linux（X11 或 Wayland）
- Python 3.10 或更新版本、`python3-venv` 和 `pip`
- 本机已登录、位于 `PATH` 中的 Codex CLI，用于读取额度和任务数据
- NVIDIA 驱动及 `nvidia-smi`，仅 GPU 监控需要

额度通过本机 `codex app-server --stdio` 接口读取。本机任务和自动化数量依赖 Codex 桌面数据库；旧版或其他安装方式可能无法提供。任务数是本机记录数，不是同时运行的 Agent 数，也不包含云端任务。Codex 接口变化时可能需要更新本项目。

## 安装与启动

```bash
git clone https://github.com/LRQ577/codex-meter.git
cd codex-meter
./install.sh
./run.sh
```

`install.sh` 在项目内创建 `.venv`，并按 `requirements.txt` 安装依赖。`run.sh` 可从任意工作目录调用。如果桌面会话的 `PATH` 找不到 `codex`，请从已配置环境的终端启动，或配置桌面会话环境变量。

## 应用菜单与登录自启动

```bash
./install-desktop.sh                # 仅安装应用菜单入口
./install-desktop.sh --autostart    # 同时启用登录自启动
./install-desktop.sh --no-autostart # 移除登录自启动
```

桌面入口使用当前项目的绝对路径。移动项目后须重新运行安装命令。部分桌面环境需要重新登录才能显示新菜单项。

## 使用与数据

拖动悬窗空白区域可以移动位置；右上角刷新按钮立即更新。CPU、内存和 GPU 每 2 秒刷新；Codex 额度与本机任务数每 60 秒刷新。待办支持添加、完成和删除。需要处理的未读 Codex 收件箱结果可同步到待办；例行自动化本身不会成为个人待办。

待办只保存在本机 `${XDG_DATA_HOME:-~/.local/share}/codex-meter/todos.json`。`CODEX_HOME` 可覆盖 Codex 数据目录，默认是 `~/.codex`。程序以只读方式访问本机 Codex 数据库，不会上传登录信息、令牌、待办或缓存。这些文件都不应提交到公开仓库。

## 验证

```bash
./.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen timeout 10s ./run.sh
```

离屏冒烟检查的退出码 `124` 表示常驻窗口运行到 `timeout` 截止；需要检查 stderr 是否有异常。完整桌面验证还应从应用菜单启动并确认悬窗出现。没有 NVIDIA GPU 时，GPU 区域会提示 `nvidia-smi` 不可用。

## 许可证

MIT，见 [LICENSE](LICENSE)。
