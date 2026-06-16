from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .requirements import build_review_payload, load_daily_review, load_status, save_review_decisions


def run_review_server(config: AppConfig, *, day: date, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> str:
    handler = make_handler(config=config)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/review/{day.isoformat()}"
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    print(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


def make_handler(*, config: AppConfig):
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/review", "/review/today"}:
                self.redirect(f"/review/{date.today().isoformat()}")
                return
            if parsed.path.startswith("/review/"):
                day = parse_day_from_path(parsed.path)
                if day is None:
                    self.respond_empty("text/plain; charset=utf-8", status=400)
                    return
                self.respond_empty("text/html; charset=utf-8")
                return
            if parsed.path.startswith("/api/review/"):
                day = parse_day_from_path(parsed.path.replace("/api", "", 1))
                if day is None:
                    self.respond_empty("application/json; charset=utf-8", status=400)
                    return
                self.respond_empty("application/json; charset=utf-8")
                return
            if parsed.path == "/api/status":
                self.respond_empty("application/json; charset=utf-8")
                return
            self.respond_empty("text/plain; charset=utf-8", status=404)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/review", "/review/today"}:
                self.redirect(f"/review/{date.today().isoformat()}")
                return
            if parsed.path.startswith("/review/"):
                day = parse_day_from_path(parsed.path)
                if day is None:
                    self.respond_text("invalid date", status=400)
                    return
                self.respond_html(render_review_page(day))
                return
            if parsed.path.startswith("/api/review/"):
                day = parse_day_from_path(parsed.path.replace("/api", "", 1))
                if day is None:
                    self.respond_json({"error": "invalid date"}, status=400)
                    return
                self.respond_json(build_review_payload(config, day))
                return
            if parsed.path == "/api/status":
                self.respond_json(load_status(storage=config.storage))
                return
            self.respond_text("not found", status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/review/"):
                day = parse_day_from_path(parsed.path.replace("/api", "", 1))
                if day is None:
                    self.respond_json({"error": "invalid date"}, status=400)
                    return
                payload = self.read_json_body()
                decisions = payload.get("decisions") if isinstance(payload, dict) else None
                if not isinstance(decisions, list):
                    self.respond_json({"error": "decisions must be an array"}, status=400)
                    return
                saved = save_review_decisions(day, decisions, config=config)
                self.respond_json({"ok": True, "saved": saved})
                return
            self.respond_text("not found", status=404)

        def read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def respond_json(self, payload: Any, *, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_html(self, html: str, *, status: int = 200) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_text(self, text: str, *, status: int = 200) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_empty(self, content_type: str, *, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    return ReviewHandler


def parse_day_from_path(path: str) -> date | None:
    try:
        value = path.rstrip("/").split("/")[-1]
        if value == "today":
            return date.today()
        return date.fromisoformat(value)
    except ValueError:
        return None


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def render_review_page(day: date) -> str:
    day_text = day.isoformat()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Work Journal Review {day_text}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #6b7280;
      --line: #d8d8d2;
      --accent: #2563eb;
      --ok: #0f766e;
      --warn: #b45309;
      --danger: #b91c1c;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #151515;
        --panel: #202020;
        --text: #ededed;
        --muted: #a3a3a3;
        --line: #3a3a3a;
        --accent: #7aa2ff;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--bg) 92%, transparent);
      backdrop-filter: blur(12px);
    }}
    .bar {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    h1 {{ font-size: 18px; margin: 0; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 18px 20px 48px; }}
    .summary {{ color: var(--muted); margin-top: 4px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 7px;
      min-height: 32px;
      padding: 6px 11px;
      cursor: pointer;
    }}
    button.primary {{ border-color: var(--accent); color: white; background: var(--accent); }}
    button.danger {{ color: var(--danger); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; }}
    .card {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }}
    .card.confirmed {{ border-color: color-mix(in srgb, var(--ok), var(--line) 55%); }}
    .card.ignored {{ opacity: .62; }}
    label {{ display: block; font-size: 12px; color: var(--muted); margin: 10px 0 4px; }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 34px;
      padding: 6px 8px;
      background: var(--bg);
      color: var(--text);
      font: inherit;
    }}
    .meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin-top: 8px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; }}
    .section-title {{ margin: 12px 0 4px; font-weight: 650; }}
    ul {{ margin: 4px 0 0 18px; padding: 0; }}
    li {{ margin: 2px 0; overflow-wrap: anywhere; }}
    .reasons {{ color: var(--warn); }}
    .empty {{ border: 1px dashed var(--line); border-radius: 8px; padding: 24px; color: var(--muted); text-align: center; }}
    .toast {{ position: fixed; right: 18px; bottom: 18px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; box-shadow: 0 8px 28px rgba(0,0,0,.18); display: none; }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>需求确认 · {day_text}</h1>
        <div class="summary" id="summary">加载中...</div>
      </div>
      <div class="actions">
        <button onclick="loadData()">刷新</button>
        <button class="primary" onclick="saveData()">保存确认</button>
      </div>
    </div>
  </header>
  <main>
    <div class="grid" id="cards"></div>
    <div class="empty" id="empty" style="display:none;">今天没有可确认的候选需求。</div>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    const day = "{day_text}";
    let payload = null;

    async function loadData() {{
      const res = await fetch(`/api/review/${{day}}`);
      payload = await res.json();
      render();
    }}

    function render() {{
      const cards = document.getElementById("cards");
      const empty = document.getElementById("empty");
      cards.innerHTML = "";
      const candidates = payload.candidates || [];
      empty.style.display = candidates.length ? "none" : "block";
      document.getElementById("summary").textContent =
        `候选 ${{payload.summary.total_candidates}} 个，待确认 ${{payload.summary.pending_candidates}} 个，事件 ${{payload.summary.event_count}} 条`;
      for (const item of candidates) {{
        cards.appendChild(renderCard(item));
      }}
    }}

    function renderCard(item) {{
      const card = document.createElement("section");
      card.className = `card ${{item.status || ""}}`;
      card.dataset.id = item.candidate_id;
      card.innerHTML = `
        <label>需求标题</label>
        <input class="title" value="${{escapeAttr(item.title || "")}}" />
        <div class="meta">
          <span class="pill">${{escapeHtml(item.project || "unknown")}}</span>
          <span class="pill">${{escapeHtml(item.requirement_type || "direct")}}</span>
          <span class="pill">置信度 ${{item.confidence}}</span>
          <span class="pill">事件 ${{item.event_count}}</span>
          <span class="pill">${{escapeHtml((item.sources || []).join(", ") || "unknown")}}</span>
        </div>
        <label>状态</label>
        <select class="status">
          <option value="confirmed">确认</option>
          <option value="pending">待确认</option>
          <option value="ignored">忽略</option>
        </select>
        <label>类型</label>
        <select class="requirement_type">
          <option value="direct">直接实现</option>
          <option value="plan-driven">方案驱动</option>
          <option value="review">审查/Review</option>
          <option value="debug">排障</option>
          <option value="docs">文档</option>
        </select>
        ${{renderList("提示", item.reasons, "reasons")}}
        ${{renderList("需求线索", [item.request, item.decision].filter(Boolean))}}
        ${{renderAnchors(item.anchors || {{}})}}
        ${{renderList("相关文件", item.files || [])}}
        <div class="actions" style="margin-top:12px;">
          <button onclick="setStatus('${{item.candidate_id}}','confirmed')">确认</button>
          <button onclick="setStatus('${{item.candidate_id}}','pending')">待确认</button>
          <button class="danger" onclick="setStatus('${{item.candidate_id}}','ignored')">忽略</button>
        </div>
      `;
      card.querySelector(".status").value = item.status || "pending";
      card.querySelector(".requirement_type").value = item.requirement_type || "direct";
      return card;
    }}

    function renderAnchors(anchors) {{
      const sections = [];
      for (const [key, values] of Object.entries(anchors)) {{
        if (Array.isArray(values) && values.length) {{
          sections.push(renderList(anchorLabel(key), values));
        }}
      }}
      return sections.join("");
    }}

    function renderList(title, values, extraClass="") {{
      if (!values || !values.length) return "";
      return `<div class="section-title ${{extraClass}}">${{escapeHtml(title)}}</div><ul>${{values.map(v => `<li>${{escapeHtml(v)}}</li>`).join("")}}</ul>`;
    }}

    function anchorLabel(key) {{
      return {{
        plan_docs: "方案文档",
        review_docs: "Review/审查",
        requirement_docs: "需求/技术文档",
        implementation_files: "实现文件"
      }}[key] || key;
    }}

    function setStatus(id, status) {{
      const card = document.querySelector(`[data-id="${{id}}"]`);
      card.querySelector(".status").value = status;
      card.classList.remove("confirmed", "ignored", "pending");
      card.classList.add(status);
    }}

    async function saveData() {{
      const decisions = Array.from(document.querySelectorAll(".card")).map(card => {{
        const source = (payload.candidates || []).find(item => item.candidate_id === card.dataset.id) || {{}};
        return {{
          candidate_id: card.dataset.id,
          title: card.querySelector(".title").value.trim(),
          project: source.project,
          requirement_type: card.querySelector(".requirement_type").value,
          status: card.querySelector(".status").value,
          event_ids: source.event_ids || [],
          anchors: source.anchors || {{}}
        }};
      }});
      const res = await fetch(`/api/review/${{day}}`, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ decisions }})
      }});
      const data = await res.json();
      if (!res.ok || !data.ok) {{
        showToast(data.error || "保存失败");
        return;
      }}
      showToast("已保存确认结果");
      await loadData();
    }}

    function showToast(text) {{
      const toast = document.getElementById("toast");
      toast.textContent = text;
      toast.style.display = "block";
      setTimeout(() => toast.style.display = "none", 2200);
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, char => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char]));
    }}
    function escapeAttr(value) {{ return escapeHtml(value); }}
    loadData();
  </script>
</body>
</html>"""
