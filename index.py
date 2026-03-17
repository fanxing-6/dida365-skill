#!/usr/bin/env python3
"""Dida365 CLI skill entrypoint."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from auth import get_access_token, load_env_file, refresh_access_token, run_oauth_flow

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment]

API_BASE = "https://api.dida365.com/open/v1"
PRIORITY_LABELS = {0: "  ", 1: "低", 3: "中", 5: "高"}


def request_dida_api(method: str, api_path: str, body: dict[str, Any] | None = None, retry: bool = True) -> Any:
    token = get_access_token()
    data = None
    headers = {"Authorization": f"Bearer {token}"}

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method in {"POST", "PUT", "PATCH"}:
        data = b""

    request = urllib.request.Request(
        f"{API_BASE}{api_path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request) as response:
            if response.status == 204:
                return None

            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8"))
            return payload.decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401 and retry and not os.environ.get("DIDA_ACCESS_TOKEN"):
            refresh_access_token()
            return request_dida_api(method, api_path, body=body, retry=False)
        raise RuntimeError(f"Dida API {method} {api_path}: HTTP {exc.code} - {text}") from exc


def list_projects() -> list[dict[str, Any]]:
    return request_dida_api("GET", "/project")


def get_project_data(project_id: str) -> dict[str, Any]:
    return request_dida_api("GET", f"/project/{project_id}/data")


def get_project(project_id: str) -> dict[str, Any]:
    return request_dida_api("GET", f"/project/{project_id}")


def get_task(project_id: str, task_id: str) -> dict[str, Any]:
    return request_dida_api("GET", f"/project/{project_id}/task/{task_id}")


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    return request_dida_api("POST", "/project", body=data)


def update_project(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return request_dida_api("POST", f"/project/{project_id}", body=data)


def delete_project(project_id: str) -> None:
    request_dida_api("DELETE", f"/project/{project_id}")


def create_task(task_data: dict[str, Any]) -> dict[str, Any]:
    return request_dida_api("POST", "/task", body=task_data)


def update_task(task_id: str, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    payload["projectId"] = project_id
    return request_dida_api("POST", f"/task/{task_id}", body=payload)


def complete_task(project_id: str, task_id: str) -> None:
    request_dida_api("POST", f"/project/{project_id}/task/{task_id}/complete")


def delete_task(project_id: str, task_id: str) -> None:
    request_dida_api("DELETE", f"/project/{project_id}/task/{task_id}")


def move_tasks(operations: list[dict[str, str]]) -> list[dict[str, Any]]:
    return request_dida_api("POST", "/task/move", body=operations)


def list_completed_tasks(
    project_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {}
    if project_ids:
        payload["projectIds"] = project_ids
    if start_date:
        payload["startDate"] = normalize_range_boundary(start_date, end_of_day=False)
    if end_date:
        payload["endDate"] = normalize_range_boundary(end_date, end_of_day=True)
    return request_dida_api("POST", "/task/completed", body=payload)


def filter_tasks(filters: dict[str, Any]) -> list[dict[str, Any]]:
    return request_dida_api("POST", "/task/filter", body=filters)


def is_task_visible_in_project(project_id: str, task_id: str) -> bool:
    for status in (0, 2):
        tasks = filter_tasks({"projectIds": [project_id], "status": [status]})
        if any(str(task.get("id")) == str(task_id) for task in tasks):
            return True
    return False


def get_today() -> list[dict[str, Any]]:
    today_str = datetime.now().date().isoformat()
    results: list[dict[str, Any]] = []

    for project in list_projects():
        project_data = get_project_data(project["id"])
        for task in project_data.get("tasks", []):
            due_day = get_task_date(task, "dueDate")
            if due_day == today_str and task.get("status") == 0:
                enriched = dict(task)
                enriched["_projectName"] = project.get("name", "")
                results.append(enriched)

    return results


def get_due_range(
    start_date: str,
    end_date: str,
    include_completed: bool = False,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    if project_id:
        project_data = get_project_data(project_id)
        projects = [
            {
                "id": project_data.get("project", {}).get("id", project_id),
                "name": project_data.get("project", {}).get("name", project_id),
                "tasks": project_data.get("tasks", []),
            }
        ]
    else:
        projects = []
        for project in list_projects():
            project_data = get_project_data(project["id"])
            projects.append(
                {
                    "id": project["id"],
                    "name": project.get("name", ""),
                    "tasks": project_data.get("tasks", []),
                }
            )

    for project in projects:
        for task in project.get("tasks", []):
            due_day = get_task_date(task, "dueDate")
            if not due_day:
                continue
            if start_date <= due_day <= end_date and (include_completed or task.get("status") == 0):
                enriched = dict(task)
                enriched["_projectName"] = project.get("name", "")
                results.append(enriched)

    results.sort(
        key=lambda task: (
            get_task_date(task, "dueDate"),
            -int(task.get("priority", 0) or 0),
            task.get("_projectName", ""),
            task.get("title", ""),
        )
    )
    return results


def get_inbox() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    projects = list_projects()
    inbox = next(
        (
            project
            for project in projects
            if project.get("kind") == "TASK" and project.get("isOwner") and project.get("inAll") is not False
        ),
        None,
    )
    if inbox is None:
        inbox = next((project for project in projects if project.get("name") in {"收集箱", "Inbox"}), None)
    if inbox is None and projects:
        inbox = projects[0]
    if inbox is None:
        raise RuntimeError("未找到收集箱项目")

    project_data = get_project_data(inbox["id"])
    return inbox, project_data.get("tasks", [])


def format_project_list(projects: list[dict[str, Any]]) -> str:
    lines = ["项目列表:\n"]
    for project in projects:
        archived = " (已归档)" if project.get("closed") else ""
        lines.append(f"  [{project['id']}] {project['name']}{archived}")
    lines.append(f"\n共 {len(projects)} 个项目")
    return "\n".join(lines)


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=datetime.now().astimezone().tzinfo)

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def get_task_timezone(task: dict[str, Any]):
    tz_name = task.get("timeZone")
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            pass
    return datetime.now().astimezone().tzinfo


def get_task_date(task: dict[str, Any], field: str) -> str:
    parsed = parse_api_datetime(task.get(field))
    if parsed is None:
        return ""
    timezone_info = get_task_timezone(task)
    if timezone_info is not None:
        parsed = parsed.astimezone(timezone_info)
    return parsed.date().isoformat()


def get_task_datetime(task: dict[str, Any], field: str) -> str:
    parsed = parse_api_datetime(task.get(field))
    if parsed is None:
        return ""
    timezone_info = get_task_timezone(task)
    if timezone_info is not None:
        parsed = parsed.astimezone(timezone_info)
    return parsed.isoformat(timespec="seconds")


def format_task_list(tasks: list[dict[str, Any]], title: str = "任务列表") -> str:
    if not tasks:
        return f"{title}: 无任务"

    lines = [f"{title}:\n"]
    for task in tasks:
        priority = PRIORITY_LABELS.get(task.get("priority"), "  ")
        due_date = get_task_date(task, "dueDate")
        due = f" 截止:{due_date}" if due_date else ""
        tags = f" [{','.join(task['tags'])}]" if task.get("tags") else ""
        project = f" ({task['_projectName']})" if task.get("_projectName") else ""
        status = " ✓" if task.get("status") == 2 else ""
        lines.append(f"  [{priority}] {task.get('title', '')}{status}{due}{tags}{project}")
        lines.append(f"       ID: {task.get('id')}  项目ID: {task.get('projectId')}")
        if task.get("content"):
            lines.append(f"       {task['content'][:80]}")
    lines.append(f"\n共 {len(tasks)} 个任务")
    return "\n".join(lines)


def format_project_detail(project_data: dict[str, Any]) -> str:
    project = project_data.get("project", {})
    tasks = project_data.get("tasks", [])
    lines = [f"项目: {project.get('name', '')} [{project.get('id', '')}]", ""]

    if not tasks:
        lines.append("  无任务")
    else:
        for task in tasks:
            priority = PRIORITY_LABELS.get(task.get("priority"), "  ")
            due_date = get_task_date(task, "dueDate")
            due = f" 截止:{due_date}" if due_date else ""
            tags = f" [{','.join(task['tags'])}]" if task.get("tags") else ""
            status = " ✓" if task.get("status") == 2 else ""
            lines.append(f"  [{priority}] {task.get('title', '')}{status}{due}{tags}")
            lines.append(f"       ID: {task.get('id')}")
            if task.get("content"):
                lines.append(f"       {task['content'][:80]}")

    lines.append(f"\n共 {len(tasks)} 个任务")
    return "\n".join(lines)


def format_project_info(project: dict[str, Any]) -> str:
    lines = [
        f"项目: {project.get('name', '')} [{project.get('id', '')}]",
        f"类型: {project.get('kind', '')}",
        f"视图: {project.get('viewMode', '')}",
    ]
    if "closed" in project:
        lines.append(f"已关闭: {'是' if project.get('closed') else '否'}")
    if project.get("color"):
        lines.append(f"颜色: {project['color']}")
    if project.get("permission"):
        lines.append(f"权限: {project['permission']}")
    if project.get("sortOrder") is not None:
        lines.append(f"排序值: {project['sortOrder']}")
    if project.get("groupId"):
        lines.append(f"分组ID: {project['groupId']}")
    return "\n".join(lines)


def format_task_detail(task: dict[str, Any]) -> str:
    lines = [
        f"任务: {task.get('title', '')} [{task.get('id', '')}]",
        f"项目ID: {task.get('projectId', '')}",
        f"状态: {'已完成' if task.get('status') == 2 else '未完成'}",
        f"优先级: {PRIORITY_LABELS.get(task.get('priority'), '无').strip() or '无'}",
    ]

    due_date = get_task_date(task, "dueDate")
    if due_date:
        lines.append(f"截止日期: {due_date}")

    start_date = get_task_datetime(task, "startDate")
    if start_date:
        lines.append(f"开始时间: {start_date}")

    completed_time = get_task_datetime(task, "completedTime")
    if completed_time:
        lines.append(f"完成时间: {completed_time}")

    if task.get("timeZone"):
        lines.append(f"时区: {task['timeZone']}")
    if task.get("desc"):
        lines.append(f"描述: {task['desc']}")
    if "isAllDay" in task:
        lines.append(f"全天: {'是' if task.get('isAllDay') else '否'}")
    if task.get("repeatFlag"):
        lines.append(f"重复规则: {task['repeatFlag']}")
    if task.get("sortOrder") is not None:
        lines.append(f"排序值: {task['sortOrder']}")
    if task.get("tags"):
        lines.append(f"标签: {', '.join(task['tags'])}")
    if task.get("reminders"):
        lines.append(f"提醒: {', '.join(task['reminders'])}")
    if task.get("content"):
        lines.append(f"内容: {task['content']}")

    items = task.get("items") or []
    if items:
        lines.append("子项:")
        for item in items:
            status = "✓" if item.get("status") == 1 else " "
            item_parts = [
                f"  [{status}] {item.get('title', '')} [{item.get('id', '')}]",
                f"status={item.get('status')}",
            ]
            if item.get("isAllDay") is not None:
                item_parts.append(f"全天={'是' if item.get('isAllDay') else '否'}")
            if item.get("sortOrder") is not None:
                item_parts.append(f"sortOrder={item.get('sortOrder')}")
            if item.get("timeZone"):
                item_parts.append(f"tz={item.get('timeZone')}")
            item_start = get_task_datetime(item, "startDate")
            if item_start:
                item_parts.append(f"start={item_start}")
            item_completed = get_task_datetime(item, "completedTime")
            if item_completed:
                item_parts.append(f"completed={item_completed}")
            lines.append(" ".join(item_parts))

    return "\n".join(lines)


def parse_args(args: list[str]) -> tuple[list[str], dict[str, Any]]:
    positional: list[str] = []
    named: dict[str, Any] = {}
    index = 0

    while index < len(args):
        arg = args[index]
        if arg.startswith("--"):
            key = arg[2:]
            next_value = args[index + 1] if index + 1 < len(args) else None
            if next_value is not None and not next_value.startswith("--"):
                named[key] = next_value
                index += 2
            else:
                named[key] = True
                index += 1
        else:
            positional.append(arg)
            index += 1

    return positional, named


def normalize_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    if "T" in date_str:
        return date_str
    offset = datetime.now().astimezone().strftime("%z") or "+0000"
    return f"{date_str}T00:00:00{offset}"


def normalize_range_boundary(date_str: str | None, end_of_day: bool) -> str | None:
    if not date_str:
        return None
    if "T" in date_str:
        return date_str
    offset = datetime.now().astimezone().strftime("%z") or "+0000"
    time_part = "23:59:59" if end_of_day else "00:00:00"
    return f"{date_str}T{time_part}{offset}"


def normalize_date_only(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").date().isoformat()


def parse_csv(value: str | None, separator: str = ",") -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(separator) if item.strip()]


def parse_json_input(raw: str | None) -> Any:
    if not raw:
        raise RuntimeError("需要通过 stdin 提供 JSON")
    return json.loads(raw)


def read_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    content = sys.stdin.read()
    return content or None


def print_usage() -> None:
    print(
        """
滴答清单 CLI 工具 v2.1.0

用法: python3 index.py <command> [args]

认证:
  auth [--code xxx]                         OAuth 2.0 授权
  check                                     检查连接状态

项目:
  projects                                  列出所有项目
  project-info {projectId}                  查看项目元数据
  project {projectId}                       查看项目详情及任务
  create-project {name} [--color x]         创建项目
  update-project {id} --name xxx            更新项目
  delete-project {id}                       删除项目

任务:
  task {projectId} {taskId}                 查看单个任务详情
  create-task {title} [options]             创建任务
  create-task-raw                           从 stdin JSON 创建高级任务
  create-checklist {title} --project {id} --items "a|b|c" [options]
                                           创建带子项的 checklist 任务
    --project {id}    指定项目
    --content {desc}  任务描述
    --due {date}      截止日期 (YYYY-MM-DD)
    --priority {N}    优先级 (0=无,1=低,3=中,5=高)
    --tags {t1,t2}    标签 (逗号分隔)
  update-task {id} --project {pid} [options]  更新任务
  update-task-raw {taskId}                  从 stdin JSON 更新高级任务
  complete-task {projectId} {taskId}        完成任务
  delete-task {projectId} {taskId}          删除任务
  move-task {fromProjectId} {toProjectId} {taskId}
                                           移动任务

查询:
  today                                     今日待办
  upcoming [days] [--project id]            未来 N 天到期任务（默认 7）
  due-range {start} {end} [--project id]    指定日期区间到期任务
  completed {start} {end} [--project id]    指定日期区间内已完成任务
  filter-tasks [--project id] [--start date] [--end date] [--priority 0,3] [--tags a,b] [--status 0,2]
                                           按条件过滤任务
  inbox                                     收集箱任务
""".strip()
    )


def command_auth(args: list[str]) -> None:
    _, named = parse_args(args[1:])
    run_oauth_flow(named.get("code"))


def command_check(args: list[str]) -> None:
    del args
    projects = list_projects()
    print(f"连接正常。共 {len(projects)} 个项目。")


def command_projects(args: list[str]) -> None:
    del args
    print(format_project_list(list_projects()))


def command_project_info(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py project-info {projectId}")
    print(format_project_info(get_project(positional[0])))


def command_project(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py project {projectId}")
    print(format_project_detail(get_project_data(positional[0])))


def command_task(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if len(positional) < 2:
        raise RuntimeError("用法: python3 index.py task {projectId} {taskId}")
    project_id = positional[0]
    task_id = positional[1]
    task = get_task(project_id, task_id)
    if not is_task_visible_in_project(project_id, task_id):
        raise RuntimeError(
            "该任务已不在项目可见结果中；OpenAPI 仍返回了缓存对象，可能该任务已删除。"
        )
    print(format_task_detail(task))


def command_create_project(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py create-project {name}")

    payload: dict[str, Any] = {"name": positional[0]}
    if named.get("color"):
        payload["color"] = named["color"]
    if named.get("kind"):
        payload["kind"] = named["kind"]

    result = create_project(payload)
    print(f"项目创建成功: {result['name']} [{result['id']}]")


def command_update_project(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py update-project {projectId} --name xxx")

    payload: dict[str, Any] = {}
    if named.get("name"):
        payload["name"] = named["name"]
    if named.get("color"):
        payload["color"] = named["color"]

    result = update_project(positional[0], payload)
    print(f"项目更新成功: {result['name']} [{result['id']}]")


def command_delete_project(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py delete-project {projectId}")
    delete_project(positional[0])
    print(f"项目已删除: {positional[0]}")


def command_create_task(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    if not positional:
        raise RuntimeError(
            "用法: python3 index.py create-task {title} [--project id] "
            "[--content desc] [--due YYYY-MM-DD] [--priority 0/1/3/5] [--tags t1,t2]"
        )

    stdin_content = read_stdin()
    payload: dict[str, Any] = {"title": positional[0]}
    if named.get("project"):
        payload["projectId"] = named["project"]
    if named.get("content"):
        payload["content"] = named["content"]
    if stdin_content:
        payload["content"] = stdin_content
    if named.get("due"):
        payload["dueDate"] = normalize_date(named["due"])
    if named.get("priority"):
        payload["priority"] = int(named["priority"])
    if named.get("tags"):
        payload["tags"] = parse_csv(named["tags"])

    result = create_task(payload)
    print(f"任务创建成功: {result['title']} [{result['id']}] 项目ID: {result.get('projectId')}")


def command_create_task_raw(args: list[str]) -> None:
    del args
    payload = parse_json_input(read_stdin())
    if not isinstance(payload, dict):
        raise RuntimeError("create-task-raw 需要 JSON object")
    result = create_task(payload)
    print(f"高级任务创建成功: {result['title']} [{result['id']}] 项目ID: {result.get('projectId')}")


def command_create_checklist(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    title = positional[0] if positional else None
    project_id = named.get("project")
    items = parse_csv(named.get("items"), separator="|")
    if not title or not project_id or not items:
        raise RuntimeError(
            '用法: python3 index.py create-checklist {title} --project {projectId} --items "子项1|子项2" '
            "[--content xxx] [--due YYYY-MM-DD] [--priority N]"
        )

    payload: dict[str, Any] = {
        "title": title,
        "projectId": str(project_id),
        "items": [{"title": item, "status": 0} for item in items],
    }
    if named.get("content"):
        payload["content"] = named["content"]
    if named.get("due"):
        payload["dueDate"] = normalize_date(named["due"])
    if named.get("priority"):
        payload["priority"] = int(named["priority"])

    result = create_task(payload)
    print(f"Checklist 创建成功: {result['title']} [{result['id']}] 项目ID: {result.get('projectId')}")


def command_update_task(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    task_id = positional[0] if positional else None
    project_id = named.get("project")
    if not task_id or not project_id:
        raise RuntimeError(
            "用法: python3 index.py update-task {taskId} --project {projectId} "
            "[--title xxx] [--content xxx] [--due YYYY-MM-DD] [--priority N] [--tags t1,t2]"
        )

    payload: dict[str, Any] = {}
    if named.get("title"):
        payload["title"] = named["title"]
    if named.get("content"):
        payload["content"] = named["content"]
    if named.get("due"):
        payload["dueDate"] = normalize_date(named["due"])
    if named.get("priority"):
        payload["priority"] = int(named["priority"])
    if named.get("tags"):
        payload["tags"] = parse_csv(named["tags"])

    result = update_task(task_id, str(project_id), payload)
    print(f"任务更新成功: {result['title']} [{result['id']}]")


def command_update_task_raw(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if not positional:
        raise RuntimeError("用法: python3 index.py update-task-raw {taskId}")
    payload = parse_json_input(read_stdin())
    if not isinstance(payload, dict):
        raise RuntimeError("update-task-raw 需要 JSON object")
    project_id = payload.get("projectId")
    if not project_id:
        raise RuntimeError("update-task-raw 的 JSON 必须包含 projectId")
    result = update_task(positional[0], str(project_id), payload)
    print(f"高级任务更新成功: {result['title']} [{result['id']}]")


def command_complete_task(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if len(positional) < 2:
        raise RuntimeError("用法: python3 index.py complete-task {projectId} {taskId}")
    complete_task(positional[0], positional[1])
    print(f"任务已完成: {positional[1]}")


def command_delete_task(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if len(positional) < 2:
        raise RuntimeError("用法: python3 index.py delete-task {projectId} {taskId}")
    delete_task(positional[0], positional[1])
    if is_task_visible_in_project(positional[0], positional[1]):
        print(f"删除请求已发送，但任务仍出现在项目可见结果中: {positional[1]}")
        return
    print(f"任务已从项目可见结果中删除: {positional[1]}")


def command_move_task(args: list[str]) -> None:
    positional, _ = parse_args(args[1:])
    if len(positional) < 3:
        raise RuntimeError("用法: python3 index.py move-task {fromProjectId} {toProjectId} {taskId}")
    result = move_tasks(
        [
            {
                "fromProjectId": positional[0],
                "toProjectId": positional[1],
                "taskId": positional[2],
            }
        ]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_today(args: list[str]) -> None:
    del args
    print(format_task_list(get_today(), "今日待办"))


def command_upcoming(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    days = int(positional[0]) if positional else 7
    if days <= 0:
        raise RuntimeError("days 必须大于 0")

    start = datetime.now().date()
    end = start + timedelta(days=days - 1)
    tasks = get_due_range(start.isoformat(), end.isoformat(), project_id=named.get("project"))
    print(format_task_list(tasks, f"未来 {days} 天到期任务 ({start.isoformat()} ~ {end.isoformat()})"))


def command_due_range(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    if len(positional) < 2:
        raise RuntimeError("用法: python3 index.py due-range {start} {end}")

    start = normalize_date_only(positional[0])
    end = normalize_date_only(positional[1])
    if start > end:
        raise RuntimeError("开始日期不能晚于结束日期")

    tasks = get_due_range(start, end, project_id=named.get("project"))
    print(format_task_list(tasks, f"到期任务 ({start} ~ {end})"))


def command_completed(args: list[str]) -> None:
    positional, named = parse_args(args[1:])
    if len(positional) < 2:
        raise RuntimeError("用法: python3 index.py completed {start} {end} [--project {projectId}]")

    start = normalize_date_only(positional[0])
    end = normalize_date_only(positional[1])
    if start > end:
        raise RuntimeError("开始日期不能晚于结束日期")

    project_ids = [str(named["project"])] if named.get("project") else None
    tasks = list_completed_tasks(project_ids=project_ids, start_date=start, end_date=end)
    if project_ids:
        project_name = get_project_data(project_ids[0]).get("project", {}).get("name", project_ids[0])
        for task in tasks:
            task.setdefault("_projectName", project_name)
    print(format_task_list(tasks, f"已完成任务 ({start} ~ {end})"))


def command_filter_tasks(args: list[str]) -> None:
    _, named = parse_args(args[1:])
    payload: dict[str, Any] = {}
    if named.get("project"):
        payload["projectIds"] = [str(named["project"])]
    if named.get("start"):
        payload["startDate"] = normalize_range_boundary(str(named["start"]), end_of_day=False)
    if named.get("end"):
        payload["endDate"] = normalize_range_boundary(str(named["end"]), end_of_day=True)
    if named.get("priority"):
        payload["priority"] = [int(item) for item in parse_csv(named["priority"])]
    if named.get("tags"):
        payload["tag"] = parse_csv(named["tags"])
    if named.get("status"):
        payload["status"] = [int(item) for item in parse_csv(named["status"])]
    if not payload:
        raise RuntimeError(
            "用法: python3 index.py filter-tasks [--project id] [--start date] [--end date] "
            "[--priority 0,3] [--tags a,b] [--status 0,2]"
        )

    tasks = filter_tasks(payload)
    print(format_task_list(tasks, "筛选结果"))


def command_inbox(args: list[str]) -> None:
    del args
    project, tasks = get_inbox()
    print(format_task_list(tasks, f"收集箱 ({project['name']})"))


COMMANDS = {
    "auth": command_auth,
    "check": command_check,
    "projects": command_projects,
    "project-info": command_project_info,
    "project": command_project,
    "task": command_task,
    "create-project": command_create_project,
    "update-project": command_update_project,
    "delete-project": command_delete_project,
    "create-task": command_create_task,
    "create-task-raw": command_create_task_raw,
    "create-checklist": command_create_checklist,
    "update-task": command_update_task,
    "update-task-raw": command_update_task_raw,
    "complete-task": command_complete_task,
    "delete-task": command_delete_task,
    "move-task": command_move_task,
    "today": command_today,
    "upcoming": command_upcoming,
    "due-range": command_due_range,
    "completed": command_completed,
    "filter-tasks": command_filter_tasks,
    "inbox": command_inbox,
}


def main() -> None:
    load_env_file()
    args = sys.argv[1:]
    command = args[0] if args else None

    if not command or command in {"-h", "--help"}:
        print_usage()
        return

    handler = COMMANDS.get(command)
    if handler is None:
        print(f"未知命令: {command}", file=sys.stderr)
        print_usage()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
