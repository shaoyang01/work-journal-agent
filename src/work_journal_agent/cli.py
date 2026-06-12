from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .events import WorkEvent, append_event, read_events, truncate_text
from .merge import group_events
from .scheduler import install_daily_schedule, install_interval_schedule, schedule_status, uninstall_daily_schedule
from .setup import configure_ai_for_config, run_interactive_setup
from .ai import generate_knowledge_topics, review_task_clusters, summarize_tasks
from .sources.codex import collect_new_codex_events, import_codex_events
from .sources.opencode import event_from_hook_payload, import_opencode_events, collect_new_opencode_events
from .uninstall import run_uninstall
from .writers.obsidian import render_daily, write_daily, write_knowledge_topic_notes


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"wj: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wj", description="Local-first work journal agent")
    parser.add_argument("--config", type=Path, help="Path to config.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    event_parser = subparsers.add_parser("event", help="Manage work events")
    event_subparsers = event_parser.add_subparsers(dest="event_command", required=True)
    add_parser = event_subparsers.add_parser("add", help="Append a work event")
    add_parser.add_argument("--source", default="manual")
    add_parser.add_argument("--type", default="note", dest="event_type")
    add_parser.add_argument("--summary", required=True)
    add_parser.add_argument("--raw-request")
    add_parser.add_argument("--decision")
    add_parser.add_argument("--cwd", default=os.getcwd())
    add_parser.add_argument("--file", action="append", default=[], dest="files")
    add_parser.add_argument("--metadata", action="append", default=[], help="key=value metadata")
    add_parser.set_defaults(func=cmd_event_add)

    note_parser = subparsers.add_parser("note", help="Append a quick manual note")
    note_parser.add_argument("summary", nargs="+", help="Note text")
    note_parser.add_argument("--source", default="manual")
    note_parser.add_argument("--cwd", default=os.getcwd())
    note_parser.add_argument("--file", action="append", default=[], dest="files")
    note_parser.set_defaults(func=cmd_note)

    generate_parser = subparsers.add_parser("generate-daily", help="Generate a daily Obsidian note")
    generate_parser.add_argument("--date", type=parse_date, default=date.today())
    generate_parser.add_argument("--dry-run", action="store_true")
    generate_parser.set_defaults(func=cmd_generate_daily)

    knowledge_parser = subparsers.add_parser("generate-knowledge", help="Generate codebase knowledge notes")
    knowledge_parser.add_argument("--date", type=parse_date, default=date.today())
    knowledge_parser.add_argument("--dry-run", action="store_true")
    knowledge_parser.set_defaults(func=cmd_generate_knowledge)

    sync_parser = subparsers.add_parser("sync", help="Import sources and generate today's note")
    sync_parser.add_argument("--date", type=parse_date, default=date.today())
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.set_defaults(func=cmd_sync)

    codex_parser = subparsers.add_parser("codex", help="Codex source commands")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)
    codex_import_parser = codex_subparsers.add_parser("import", help="Import Codex sessions for a day")
    codex_import_parser.add_argument("--date", type=parse_date, default=date.today())
    codex_import_parser.add_argument("--sessions-root", type=Path)
    codex_import_parser.set_defaults(func=cmd_codex_import)

    opencode_parser = subparsers.add_parser("opencode", help="OpenCode source commands")
    opencode_subparsers = opencode_parser.add_subparsers(dest="opencode_command", required=True)
    opencode_import_parser = opencode_subparsers.add_parser("import", help="Import OpenCode storage for a day")
    opencode_import_parser.add_argument("--date", type=parse_date, default=date.today())
    opencode_import_parser.add_argument("--storage-root", type=Path)
    opencode_import_parser.set_defaults(func=cmd_opencode_import)
    opencode_hook_parser = opencode_subparsers.add_parser("hook", help="Read an OpenCode plugin event JSON from stdin")
    opencode_hook_parser.add_argument("--event-type", default=None, help="Override normalized work event type")
    opencode_hook_parser.set_defaults(func=cmd_opencode_hook)

    ai_parser = subparsers.add_parser("ai", help="Manage AI summarization")
    ai_subparsers = ai_parser.add_subparsers(dest="ai_command", required=True)
    ai_setup_parser = ai_subparsers.add_parser("setup", help="Configure DeepSeek summarization")
    ai_setup_parser.add_argument("--key", help="DeepSeek API key. Prefer interactive input to avoid shell history.")
    ai_setup_parser.add_argument("--no-schedule", action="store_true", help="Do not reinstall background sync")
    ai_setup_parser.set_defaults(func=cmd_ai_setup)
    ai_disable_parser = ai_subparsers.add_parser("disable", help="Disable AI summarization")
    ai_disable_parser.set_defaults(func=cmd_ai_disable)

    hook_parser = subparsers.add_parser("claude-hook", help="Read Claude Code hook JSON from stdin and append an event")
    hook_parser.add_argument("--event-type", default=None, help="Override hook event type")
    hook_parser.set_defaults(func=cmd_claude_hook)

    list_parser = subparsers.add_parser("list", help="List events for a day")
    list_parser.add_argument("--date", type=parse_date, default=date.today())
    list_parser.set_defaults(func=cmd_list)

    setup_parser = subparsers.add_parser("setup", help="Interactive local setup")
    setup_parser.add_argument("--yes", action="store_true", help="Accept defaults without prompting")
    setup_parser.add_argument("--schedule", action="store_true", default=None, help="Install daily auto generation")
    setup_parser.add_argument("--no-schedule", action="store_false", dest="schedule", help="Skip daily auto generation")
    setup_parser.add_argument("--every-minutes", type=int, default=60, help="Background writer interval")
    setup_parser.add_argument("--active-from", help="Only run background sync at or after HH:MM")
    setup_parser.add_argument("--active-to", help="Only run background sync at or before HH:MM")
    setup_parser.set_defaults(func=cmd_setup)

    schedule_parser = subparsers.add_parser("schedule", help="Manage daily auto generation")
    schedule_subparsers = schedule_parser.add_subparsers(dest="schedule_command", required=True)
    schedule_install_parser = schedule_subparsers.add_parser("install", help="Install daily auto generation")
    schedule_install_parser.add_argument("--time", help="Daily time, HH:MM")
    schedule_install_parser.add_argument("--every-minutes", type=int, default=60, help="Refresh interval when --time is omitted")
    schedule_install_parser.add_argument("--active-from", help="Only run interval sync at or after HH:MM")
    schedule_install_parser.add_argument("--active-to", help="Only run interval sync at or before HH:MM")
    schedule_install_parser.add_argument("--no-load", action="store_true", help="Write schedule file without loading it")
    schedule_install_parser.set_defaults(func=cmd_schedule_install)
    schedule_uninstall_parser = schedule_subparsers.add_parser("uninstall", help="Remove daily auto generation")
    schedule_uninstall_parser.set_defaults(func=cmd_schedule_uninstall)
    schedule_status_parser = schedule_subparsers.add_parser("status", help="Show daily auto generation status")
    schedule_status_parser.set_defaults(func=cmd_schedule_status)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove background integration")
    uninstall_parser.add_argument("--remove-config", action="store_true", help="Also remove config.toml")
    uninstall_parser.add_argument("--remove-data", action="store_true", help="Also remove inbox, logs, and output data")
    uninstall_parser.add_argument("--claude-settings", type=Path, help="Path to Claude Code settings.json")
    uninstall_parser.add_argument("--opencode-plugin", type=Path, help="Path to generated OpenCode plugin")
    uninstall_parser.set_defaults(func=cmd_uninstall)
    return parser


def cmd_event_add(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    metadata = parse_metadata(args.metadata)
    event = WorkEvent.create(
        source=args.source,
        event_type=args.event_type,
        summary=args.summary,
        cwd=args.cwd,
        raw_request=truncate_text(args.raw_request, config.privacy.max_raw_request_chars),
        decision=args.decision,
        files=args.files,
        metadata=metadata,
    )
    append_event(config.storage.inbox_path, event)
    print(event.to_json_line())


def cmd_note(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    event = WorkEvent.create(
        source=args.source,
        event_type="note",
        summary=" ".join(args.summary),
        cwd=args.cwd,
        files=args.files,
    )
    append_event(config.storage.inbox_path, event)
    print(event.to_json_line())


def cmd_generate_daily(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    events = [event for event in read_events(config.storage.inbox_path) if event.occurred_at.date() == args.date]
    tasks = group_events(events, min_keyword_overlap=config.merge.min_keyword_overlap)
    cluster_result = review_task_clusters(config, tasks)
    tasks = cluster_result.tasks
    ai_result = summarize_tasks(config, tasks)
    if args.dry_run:
        if should_print_cluster_message(cluster_result.message):
            print(cluster_result.message)
        if ai_result.enabled:
            print(ai_result.message)
        print(render_daily(args.date, tasks))
        return
    target = write_daily(config, args.date, tasks, write_knowledge=False)
    print(str(target))


def cmd_generate_knowledge(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if not config.ai.knowledge_enabled:
        print("AI knowledge notes disabled")
        return
    if not config.obsidian.write_knowledge_notes:
        print("Knowledge notes disabled")
        return
    events = [event for event in read_events(config.storage.inbox_path) if event.occurred_at.date() == args.date]
    tasks = group_events(events, min_keyword_overlap=config.merge.min_keyword_overlap)
    ai_result = summarize_tasks(config, tasks)
    knowledge_result = generate_knowledge_topics(config, tasks)
    if args.dry_run:
        if ai_result.enabled:
            print(ai_result.message)
        if knowledge_result.enabled:
            print(knowledge_result.message)
        print(render_daily(args.date, tasks))
        return
    targets = write_knowledge_topic_notes(config, args.date, tasks, topics=knowledge_result.topics if knowledge_result.enabled else [])
    if ai_result.enabled:
        print(ai_result.message)
    if knowledge_result.enabled:
        print(knowledge_result.message)
    for target in targets:
        print(str(target))


def cmd_sync(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    codex_result = empty_import_result("codex")
    if config.sources.codex.enabled:
        codex_root = config.sources.codex.sessions_root
        if args.dry_run:
            codex_result = collect_new_codex_events(config, day=args.date, sessions_root=codex_root)
        else:
            codex_result = import_codex_events(config, day=args.date, sessions_root=codex_root)
    opencode_result = empty_import_result("opencode")
    if config.sources.opencode.enabled:
        opencode_root = config.sources.opencode.storage_root
        if args.dry_run:
            opencode_result = collect_new_opencode_events(config, day=args.date, storage_root=opencode_root)
        else:
            opencode_result = import_opencode_events(config, day=args.date, storage_root=opencode_root)
    events = [event for event in read_events(config.storage.inbox_path) if event.occurred_at.date() == args.date]
    if args.dry_run:
        events.extend(codex_result.events)
        events.extend(opencode_result.events)
    tasks = group_events(events, min_keyword_overlap=config.merge.min_keyword_overlap)
    cluster_result = review_task_clusters(config, tasks)
    tasks = cluster_result.tasks
    ai_result = summarize_tasks(config, tasks)
    if args.dry_run:
        print(f"Imported Codex events: {codex_result.imported_events} from {codex_result.scanned_files} files")
        print(f"Imported OpenCode events: {opencode_result.imported_events} from {opencode_result.scanned_files} files")
        if should_print_cluster_message(cluster_result.message):
            print(cluster_result.message)
        if ai_result.enabled:
            print(ai_result.message)
        print(render_daily(args.date, tasks))
        return
    target = write_daily(config, args.date, tasks, write_knowledge=False)
    print(f"Imported Codex events: {codex_result.imported_events} from {codex_result.scanned_files} files")
    print(f"Imported OpenCode events: {opencode_result.imported_events} from {opencode_result.scanned_files} files")
    if should_print_cluster_message(cluster_result.message):
        print(cluster_result.message)
    if ai_result.enabled:
        print(ai_result.message)
    print(str(target))


def cmd_codex_import(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    result = import_codex_events(config, day=args.date, sessions_root=args.sessions_root or config.sources.codex.sessions_root)
    print(f"Imported Codex events: {result.imported_events} from {result.scanned_files} files")


def cmd_opencode_import(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    result = import_opencode_events(config, day=args.date, storage_root=args.storage_root or config.sources.opencode.storage_root)
    print(f"Imported OpenCode events: {result.imported_events} from {result.scanned_files} files")


def cmd_opencode_hook(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    payload = json.load(sys.stdin)
    event = event_from_hook_payload(payload, config=config, event_type_override=args.event_type)
    append_event(config.storage.inbox_path, event)
    print(event.to_json_line())


def cmd_ai_setup(args: argparse.Namespace) -> None:
    key = args.key
    if key is None:
        import getpass

        key = getpass.getpass("请输入 DeepSeek API Key（输入不会显示）: ").strip()
    if not key:
        raise ValueError("DeepSeek API Key must not be empty")
    config_path = configure_ai_for_config(config_path=args.config, deepseek_api_key=key, enabled=True)
    print(f"AI summary enabled in {config_path}")
    print(f"Secret saved to {config_path.parent / 'secrets.env'}")
    if not args.no_schedule:
        project_root = Path(__file__).resolve().parents[2]
        result = install_interval_schedule(project_root=project_root, every_minutes=60, load=True)
        print(result.message)


def cmd_ai_disable(args: argparse.Namespace) -> None:
    config_path = configure_ai_for_config(config_path=args.config, deepseek_api_key="", enabled=False)
    print(f"AI summary disabled in {config_path}")


def cmd_claude_hook(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    payload = json.load(sys.stdin)
    event = event_from_claude_payload(payload, args.event_type, config.privacy.max_raw_request_chars, config.privacy.store_transcript_paths)
    append_event(config.storage.inbox_path, event)
    print(event.to_json_line())


def cmd_list(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    events = [event for event in read_events(config.storage.inbox_path) if event.occurred_at.date() == args.date]
    for event in events:
        print(event.to_json_line())


def cmd_setup(args: argparse.Namespace) -> None:
    project_root = Path(__file__).resolve().parents[2]
    run_interactive_setup(
        project_root=project_root,
        yes=args.yes,
        schedule=args.schedule,
        every_minutes=args.every_minutes,
        active_from=args.active_from,
        active_to=args.active_to,
    )


def cmd_schedule_install(args: argparse.Namespace) -> None:
    project_root = Path(__file__).resolve().parents[2]
    if args.time:
        result = install_daily_schedule(project_root=project_root, time_text=args.time, load=not args.no_load)
    else:
        result = install_interval_schedule(
            project_root=project_root,
            every_minutes=args.every_minutes,
            active_from=args.active_from,
            active_to=args.active_to,
            load=not args.no_load,
        )
    print(result.message)


def cmd_schedule_uninstall(args: argparse.Namespace) -> None:
    result = uninstall_daily_schedule()
    print(result.message)


def cmd_schedule_status(args: argparse.Namespace) -> None:
    print(schedule_status())


def cmd_uninstall(args: argparse.Namespace) -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = run_uninstall(
        project_root=project_root,
        remove_config=args.remove_config,
        remove_data=args.remove_data,
        claude_settings_path=args.claude_settings,
        opencode_plugin_path=args.opencode_plugin,
    )
    print("Uninstall complete.")
    print(f"- schedule removed: {result.schedule_removed}")
    print(f"- Claude hooks removed: {result.claude_hooks_removed}")
    print(f"- OpenCode plugin removed: {result.opencode_plugin_removed}")
    print(f"- config removed: {result.config_removed}")
    print(f"- data removed: {result.data_removed}")


def event_from_claude_payload(
    payload: dict[str, Any],
    event_type_override: str | None,
    max_raw_request_chars: int,
    store_transcript_paths: bool,
) -> WorkEvent:
    hook_event = event_type_override or str(payload.get("hook_event_name") or payload.get("event") or "note")
    cwd = payload.get("cwd")
    metadata: dict[str, Any] = {
        "session_id": payload.get("session_id"),
    }
    if store_transcript_paths and payload.get("transcript_path"):
        metadata["transcript_path"] = payload.get("transcript_path")

    prompt = payload.get("prompt")
    tool_name = payload.get("tool_name")
    files = extract_files(payload)

    if prompt:
        summary = summarize_text(str(prompt), "Claude 用户需求")
        raw_request = truncate_text(str(prompt), max_raw_request_chars)
    elif tool_name:
        summary = f"Claude 执行工具：{tool_name}"
        raw_request = None
    else:
        summary = f"Claude 事件：{hook_event}"
        raw_request = None

    return WorkEvent.create(
        source="claude",
        event_type=hook_event,
        summary=summary,
        cwd=str(cwd) if cwd else None,
        raw_request=raw_request,
        files=files,
        metadata={key: value for key, value in metadata.items() if value},
    )


def extract_files(payload: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for key in ("file_path", "path"):
        value = payload.get(key)
        if isinstance(value, str):
            files.append(value)
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("file_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str):
                files.append(value)
    return list(dict.fromkeys(files))


def summarize_text(value: str, prefix: str) -> str:
    clean = " ".join(value.split())
    if len(clean) <= 80:
        return clean
    return f"{prefix}：{clean[:77].rstrip()}…"


def parse_metadata(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"metadata must be key=value: {value}")
        key, raw = value.split("=", 1)
        result[key.strip()] = raw.strip()
    return result


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


class EmptyImportResult:
    scanned_files = 0
    imported_events = 0
    events: tuple[WorkEvent, ...] = ()


def empty_import_result(source: str) -> EmptyImportResult:
    return EmptyImportResult()


def should_print_cluster_message(message: str) -> bool:
    return bool(message) and message != "AI cluster review skipped" and message != "AI cluster review disabled"


if __name__ == "__main__":
    main()
