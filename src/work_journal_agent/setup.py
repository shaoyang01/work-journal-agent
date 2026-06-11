from __future__ import annotations

import json
import os
import platform
import shutil
import getpass
from dataclasses import dataclass
from pathlib import Path

from .config import default_config_path, default_data_dir, expand_path
from .scheduler import ScheduleResult, install_interval_schedule


CLAUDE_HOOK_EVENTS = ("UserPromptSubmit", "PostToolUse", "Stop")


@dataclass(frozen=True)
class SetupResult:
    config_path: Path
    inbox_path: Path
    output_dir: Path
    obsidian_vault: Path | None
    claude_settings_path: Path | None
    claude_hooks_enabled: bool
    schedule_result: ScheduleResult | None


def run_interactive_setup(
    *,
    project_root: Path,
    yes: bool = False,
    schedule: bool | None = None,
    every_minutes: int = 15,
) -> SetupResult:
    print("work-journal-agent 配置向导")
    print("接下来会生成本机配置，并可选配置 Claude Code hooks 和后台自动写入器。")
    print("")

    config_path = ask_path(
        "配置文件保存位置",
        default_config_path(),
        yes=yes,
    )
    data_dir = default_data_dir()
    inbox_path = ask_path(
        "事件 inbox 文件位置",
        data_dir / "inbox" / "events.jsonl",
        yes=yes,
    )
    output_dir = ask_path(
        "未配置 Obsidian 时的备用输出目录",
        data_dir / "out",
        yes=yes,
    )
    vault_text = ask_text(
        "Obsidian vault 根目录，暂时不配置可直接回车",
        "",
        yes=yes,
    )
    obsidian_vault = expand_path(vault_text) if vault_text.strip() else None
    daily_dir = ask_text("Daily 笔记目录名", "Daily", yes=yes)
    task_dir = ask_text("独立任务笔记目录名", "Tasks", yes=yes)
    write_task_notes = ask_bool("是否额外生成独立任务笔记", False, yes=yes)
    enable_ai = ask_bool("是否启用 DeepSeek AI 分析，让它帮助整理每日工作摘要", False, yes=yes)
    deepseek_api_key = ""
    if enable_ai:
        deepseek_api_key = ask_required_secret(
            "DeepSeek API Key",
            env_name="DEEPSEEK_API_KEY",
            yes=yes,
        )
    enable_claude_hooks = ask_bool("是否自动配置 Claude Code 采集 hooks", True, yes=yes)
    if schedule is None:
        enable_schedule = ask_bool("是否安装后台自动写入器，重启后自动恢复", platform.system() == "Darwin", yes=yes)
    else:
        enable_schedule = schedule

    claude_settings_path: Path | None = None
    if enable_claude_hooks:
        claude_settings_path = ask_path(
            "Claude Code settings.json 位置",
            Path.home() / ".claude" / "settings.json",
            yes=yes,
        )

    create_config(
        config_path=config_path,
        inbox_path=inbox_path,
        output_dir=output_dir,
        obsidian_vault=obsidian_vault,
        daily_dir=daily_dir,
        task_dir=task_dir,
        write_task_notes=write_task_notes,
        enable_ai=enable_ai,
    )
    if enable_ai and deepseek_api_key:
        create_secrets_file(config_path.parent / "secrets.env", deepseek_api_key)
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if obsidian_vault:
        (obsidian_vault / daily_dir).mkdir(parents=True, exist_ok=True)
        if write_task_notes:
            (obsidian_vault / task_dir).mkdir(parents=True, exist_ok=True)

    if enable_claude_hooks and claude_settings_path:
        configure_claude_hooks(
            settings_path=claude_settings_path,
            project_root=project_root,
        )
    schedule_result: ScheduleResult | None = None
    if enable_schedule:
        schedule_result = install_interval_schedule(
            project_root=project_root,
            every_minutes=every_minutes,
            load=True,
        )

    result = SetupResult(
        config_path=config_path,
        inbox_path=inbox_path,
        output_dir=output_dir,
        obsidian_vault=obsidian_vault,
        claude_settings_path=claude_settings_path,
        claude_hooks_enabled=enable_claude_hooks,
        schedule_result=schedule_result,
    )
    print("")
    print("配置完成。")
    print(f"- 配置文件：{result.config_path}")
    print(f"- 事件 inbox：{result.inbox_path}")
    print(f"- 输出位置：{result.obsidian_vault or result.output_dir}")
    if result.claude_hooks_enabled:
        print(f"- Claude Code settings：{result.claude_settings_path}")
    if result.schedule_result:
        print(f"- 后台自动写入器：{result.schedule_result.message}")
    if enable_ai:
        print("- DeepSeek AI 分析：已启用")
    else:
        print("- DeepSeek AI 分析：未启用，将使用本地规则摘要")
    return result


def create_config(
    *,
    config_path: Path,
    inbox_path: Path,
    output_dir: Path,
    obsidian_vault: Path | None,
    daily_dir: str,
    task_dir: str,
    write_task_notes: bool,
    enable_ai: bool,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "[storage]",
            f'inbox_path = "{toml_string(inbox_path)}"',
            f'output_dir = "{toml_string(output_dir)}"',
            "",
            "[obsidian]",
            f'vault_path = "{toml_string(obsidian_vault) if obsidian_vault else ""}"',
            f'daily_dir = "{toml_string(daily_dir)}"',
            f'task_dir = "{toml_string(task_dir)}"',
            f"write_task_notes = {str(write_task_notes).lower()}",
            "",
            "[privacy]",
            "max_raw_request_chars = 500",
            "store_transcript_paths = true",
            "",
            "[merge]",
            "min_keyword_overlap = 1",
            "",
            "[ai]",
            f"enabled = {str(enable_ai).lower()}",
            'provider = "deepseek"',
            'base_url = "https://api.deepseek.com"',
            'model = "deepseek-v4-flash"',
            'api_key_env = "DEEPSEEK_API_KEY"',
            "timeout_seconds = 30",
            "",
        ]
    )
    config_path.write_text(content, encoding="utf-8")


def create_secrets_file(path: Path, deepseek_api_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"export DEEPSEEK_API_KEY={shell_single_quote(deepseek_api_key)}\n", encoding="utf-8")
    path.chmod(0o600)


def configure_ai_for_config(*, config_path: Path | None, deepseek_api_key: str, enabled: bool = True) -> Path:
    path = config_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = upsert_ai_config(text, enabled=enabled)
    path.write_text(text, encoding="utf-8")
    if deepseek_api_key:
        create_secrets_file(path.parent / "secrets.env", deepseek_api_key)
    return path


def upsert_ai_config(text: str, *, enabled: bool) -> str:
    block = "\n".join(
        [
            "[ai]",
            f"enabled = {str(enabled).lower()}",
            'provider = "deepseek"',
            'base_url = "https://api.deepseek.com"',
            'model = "deepseek-v4-flash"',
            'api_key_env = "DEEPSEEK_API_KEY"',
            "timeout_seconds = 30",
            "",
        ]
    )
    if "[ai]" not in text:
        return text.rstrip() + "\n\n" + block

    lines = text.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "[ai]":
            result.extend(block.rstrip().splitlines())
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("["):
                index += 1
            continue
        result.append(lines[index])
        index += 1
    return "\n".join(result).rstrip() + "\n"


def configure_claude_hooks(*, settings_path: Path, project_root: Path) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = read_json_object(settings_path)
    hooks = data.setdefault("hooks", {})
    for event_name in CLAUDE_HOOK_EVENTS:
        event_hooks = hooks.setdefault(event_name, [])
        add_hook_command(event_hooks, claude_hook_command(project_root, event_name))
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remove_claude_hooks(*, settings_path: Path, project_root: Path) -> bool:
    if not settings_path.exists():
        return False
    data = read_json_object(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event_name in CLAUDE_HOOK_EVENTS:
        event_hooks = hooks.get(event_name)
        if not isinstance(event_hooks, list):
            continue
        filtered = [item for item in event_hooks if not hook_entry_owned_by_work_journal(item, project_root)]
        if len(filtered) != len(event_hooks):
            changed = True
            if filtered:
                hooks[event_name] = filtered
            else:
                hooks.pop(event_name, None)
    if changed:
        if not hooks:
            data.pop("hooks", None)
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size == 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        raise ValueError(f"invalid JSON in {path}; backup created at {backup}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def add_hook_command(event_hooks: list, command: str) -> None:
    if any(existing_hook_matches(item, command) for item in event_hooks):
        return
    event_hooks.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                }
            ],
        }
    )


def existing_hook_matches(item: object, command: str) -> bool:
    if not isinstance(item, dict):
        return False
    hooks = item.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("command") == command:
            return True
    return False


def hook_entry_owned_by_work_journal(item: object, project_root: Path) -> bool:
    if not isinstance(item, dict):
        return False
    hooks = item.get("hooks")
    if not isinstance(hooks, list):
        return False
    project_text = str(project_root)
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command") or "")
        if "work-journal-agent" in command:
            return True
        if project_text in command and "claude-hook" in command:
            return True
        if "wj claude-hook" in command:
            return True
        if "work_journal_agent claude-hook" in command:
            return True
    return False


def claude_hook_command(project_root: Path, event_name: str) -> str:
    if platform.system() == "Windows":
        hook_path = project_root / "hooks" / "claude" / "hook.ps1"
        if hook_path.exists():
            return f'powershell -ExecutionPolicy Bypass -File "{hook_path}" {event_name}'
        return f"wj claude-hook --event-type {event_name}"
    hook_path = project_root / "hooks" / "claude" / "hook.sh"
    if hook_path.exists():
        return f'"{hook_path}" {event_name}'
    return f"wj claude-hook --event-type {event_name}"


def ask_path(label: str, default: Path, *, yes: bool) -> Path:
    return expand_path(ask_text(label, str(default), yes=yes))


def ask_text(label: str, default: str, *, yes: bool) -> str:
    if yes:
        return default
    prompt = f"{label}（直接回车使用默认：{default}）: " if default else f"{label}（可直接回车跳过）: "
    value = input(prompt).strip()
    return value or default


def ask_bool(label: str, default: bool, *, yes: bool) -> bool:
    if yes:
        return default
    default_text = "是" if default else "否"
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label}（直接回车={default_text}，{suffix}）: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def ask_secret(label: str, *, yes: bool) -> str:
    if yes:
        return ""
    return getpass.getpass(f"{label}（输入不会显示；不想保存可直接回车）: ").strip()


def ask_required_secret(label: str, *, env_name: str, yes: bool) -> str:
    if yes:
        return ""
    if os.environ.get(env_name):
        return getpass.getpass(f"{label}（输入不会显示；直接回车使用已有环境变量 {env_name}）: ").strip()
    while True:
        value = getpass.getpass(f"{label}（输入不会显示；启用 AI 分析必须提供）: ").strip()
        if value:
            return value
        print(f"未检测到环境变量 {env_name}，启用 AI 分析需要提供 API Key。")


def toml_string(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
