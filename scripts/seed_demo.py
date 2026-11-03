#!/usr/bin/env python3
"""Fill AgentTrail with demo data. Use --force to replace existing chat data."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatvault.auth import ensure_default_admin
from chatvault.backup import create_backup
from chatvault.constants import AGENT_DEFINITIONS
from chatvault.db import connection, init_db, log_system, set_setting
from chatvault.ingestion import import_batch
from chatvault.utils import json_dumps, utc_now


DEMO = [
    {
        "agent": "codex", "external": "codex-demo-001",
        "title": "为 ESP32-C3 设计局域网唤醒工具",
        "project": "esp32c3-lanweaker", "cwd": "D:/prj-win/esp32c3-lanweaker",
        "model": "deepseek/deepseek-v4.1-flash", "days": 0, "hours": 1,
        "events": [
            ("user", "请通读项目代码库，梳理模块划分、依赖关系和外部集成点，然后绘制架构图、0 层与 1 层数据流图。", {}),
            ("assistant", "我会按源码建模流程处理：先盘点入口、网络适配层、目标状态域和云平台适配，再以源码证据生成可编辑的多页 draw.io，最后做结构校验和导出目检。", {"in": 18240, "out": 860}),
            ("tool", "exec_command", "call-codex-01", {"cmd": "git status --short --branch", "workdir": "D:/prj-win/esp32c3-lanweaker"}, "## main"),
            ("assistant", "仓库处于干净的 main 分支。架构可归为八块：入口与命令编排、输入适配、网络适应层、目标状态域、探测服务、魔术包输出、云平台适配、持久化与平台组件。命令处理与网络发送解耦，目标状态是唯一事实来源。", {"in": 3910, "out": 420}),
            ("tool", "read_file", "call-codex-02", {"path": "D:/prj-win/esp32c3-lanweaker/src/main.cpp", "limit": 240}, "main.cpp: 初始化 NVS、Wi-Fi 配网、UDP 监听与 MQTT 客户端。命令入口 CommandRouter::dispatch，目标状态 TargetStore::apply，魔术包 WakeService::send。"),
            ("assistant", "关键路径已确认：CommandRouter 接收命令后写入 TargetStore，ProbeService 定时探测在线状态，WakeService 通过 UDP:9 发送魔术包。0 层图只保留外部实体，1 层图展开七个处理过程。", {"in": 12450, "out": 1420}),
        ],
    },
    {
        "agent": "codex", "external": "codex-demo-002",
        "title": "重构 JSONL 采集器并补齐去重",
        "project": "agenttrail", "cwd": "D:/prj-win/AI聊天记录收集器",
        "model": "deepseek/deepseek-v4.1-flash", "days": 7, "hours": 2,
        "events": [
            ("user", "把 Codex 和 Claude 的 JSONL 读取做成增量游标，避免每次全量重扫，同时保证重复行不会重复入库。", {}),
            ("assistant", "我会把文件读取拆成两层：字节游标只消费完整换行，事件 fingerprint 负责跨次幂等。这样即使压缩来源需要重扫，也不会污染统计。", {"in": 8020, "out": 510}),
            ("tool", "apply_patch", "call-codex-03", {"file": "chatvault/collectors.py", "change": "read complete lines from offset; persist next cursor"}, "Success. Updated collectors.py and ingestion.py. 2 files changed, 184 insertions, 23 deletions."),
            ("assistant", "已实现完整行游标、文件缩短回退、版本指纹去重和来源状态记录。下一次同步只读取新增字节，重复事件进入 duplicates 统计而不会增加事件总数。", {"in": 6230, "out": 380}),
        ],
    },
    {
        "agent": "claudecode", "external": "claude-demo-001",
        "title": "从户型视频反推可用面积",
        "project": "rental-space-analysis", "cwd": "D:/prj-win/租房视频分析",
        "model": "claude-sonnet-4-5", "days": 0, "hours": 2,
        "events": [
            ("user", "逐步分析待分析户型视频。抽帧按每秒至少 1 帧，恢复房间数量、门窗位置、动线和真实使用面积，最后生成可交互的三维模型。", {}),
            ("assistant", "我会先建立视频清单和取帧索引，再做镜头切分与关键帧筛选。面积估算以门洞宽度为锚点并给出误差带；无法判定的角落标记为不可判定，而不是猜测。", {"in": 22100, "out": 1120}),
            ("tool", "Bash", "call-claude-01", {"command": "ffprobe -v error -show_entries format=duration -of csv=p=0 input/room-tour.mp4", "description": "读取视频时长"}, "142.6"),
            ("assistant", "视频时长 142.6 秒。首轮按 1 fps 抽帧得到 143 帧，再以清晰度与 ORB 共视率筛选关键帧。当前识别到客厅、主卧、次卧、厨房、卫生间五个功能空间，门洞宽度估计 0.86m。", {"in": 16400, "out": 940}),
            ("tool", "Write", "call-claude-02", {"file_path": "output/floorplan-3d/js/data/layout.js", "content": "export const layout = { rooms: 5, usableArea: 78.4 }"}, "Created file output/floorplan-3d/js/data/layout.js"),
            ("assistant", "已生成分析报告和三维场景数据源。综合三路交叉估算，真实可用面积约 78.4 平方米，误差带正负 8%。客厅与主卧连通区域的推拉门轨道存在遮挡，标记为需要现场复核。", {"in": 18200, "out": 1280}),
        ],
    },
    {
        "agent": "claudecode", "external": "claude-demo-002",
        "title": "审查导出接口的注入风险",
        "project": "agenttrail", "cwd": "D:/prj-win/AI聊天记录收集器",
        "model": "claude-opus-4-1", "days": 4, "hours": 1,
        "events": [
            ("user", "审查导出接口：用户可控参数是否会进入 SQL 或文件名？CSV 是否可能被公式注入？", {}),
            ("assistant", "我会重点看三处：筛选条件拼接、Content-Disposition 文件名、CSV 单元格前缀。SQL 使用参数绑定，但导出内容本身可能被公式解释，需要在 CSV 写入层做防护。", {"in": 9860, "out": 640}),
            ("tool", "Read", "call-claude-03", {"file_path": "chatvault/exporting.py"}, "def _write_events_csv(...):\n    writer.writerow(CSV_COLUMNS)\n    ..."),
            ("assistant", "确认筛选参数全部绑定，不存在字符串拼接注入。仍建议补充两点：对以等号、加号、减号、@ 开头的单元格加单引号；下载文件名固定为服务端生成，不接受用户输入。", {"in": 11200, "out": 520}),
        ],
    },
    {
        "agent": "opencode", "external": "ses_demo_opencode_001",
        "title": "配置多 Provider 与模型路由",
        "project": "opencode-provider", "cwd": "C:/Users/xinquan/Documents/Default Project",
        "model": "mimo-v2.6-flash-free", "days": 1, "hours": 4,
        "events": [
            ("user", "为 OpenCode 配置新的 Provider 与模型路由，要求保留默认模型回退，并验证工具调用是否正常。", {}),
            ("assistant", "我会先读取现有配置，确认 provider 与 model 的命名空间，再以最小改动加入新路由；最后用一条只读命令验证工具调用链。", {"in": 15420, "out": 390}),
            ("tool", "write", "call-oc-01", {"path": "opencode.json", "content": "{ providers: { commandcode: { models: [v4.1] } } }"}, "Created file successfully: opencode.json"),
            ("assistant", "配置已写入，并保留了 opencode 默认模型作为 fallback。工具调用验证通过，新的 Provider 能够正确路由到支持 function calling 的模型。", {"in": 9200, "out": 360}),
            ("user", "不要改全局配置，只对当前项目生效。", {}),
            ("assistant", "收到。已把配置移动到项目级 opencode.json，全局配置保持原样；这样其他项目不会受到影响。", {"in": 3600, "out": 210}),
        ],
    },
    {
        "agent": "opencode", "external": "ses_demo_opencode_002",
        "title": "临时文件清理与工作区检查",
        "project": "workspace-maintenance", "cwd": "C:/Users/xinquan/AppData/Local/Temp",
        "model": "deepseek-v4.1-flash", "days": 8, "hours": 2,
        "events": [
            ("user", "只回答一个词：OK。不要改任何文件。", {}),
            ("assistant", "OK", {"in": 1240, "out": 3}),
        ],
    },
    {
        "agent": "workbuddy", "external": "wb-demo-001",
        "title": "生成赣南脐橙店铺月度经营复盘",
        "project": "gannan-orange-ops", "cwd": "D:/prj-win/赣南脐橙店铺运营",
        "model": "hunyuan-turbos", "days": 2, "hours": 5,
        "events": [
            ("user", "基于本月订单、退款和投放数据，生成一份经营复盘。重点解释 GMV 增长但净利率下降的原因，并给出下周动作清单。", {}),
            ("assistant", "我会先拉取订单与投放日报，按渠道拆分 GMV、退款率、履约成本和投放 ROI，再定位净利率下降的主因。结论会区分已证实和待验证。", {"in": 16420, "out": 760}),
            ("tool", "analytics_query", "call-wb-01", {"dataset": "orders_daily", "range": "2026-10-01..2026-10-31", "group_by": ["channel"]}, "自然流量 GMV 186400，退款率 2.1%；信息流 GMV 248900，退款率 6.7%，ROI 1.42；达人分销 GMV 96200，退款率 3.8%，ROI 2.18。履约成本 78200，包装成本 31400。"),
            ("assistant", "核心结论：GMV 环比增长 18.6%，但净利率从 21.4% 降至 16.8%。主要拖累来自信息流渠道：退款率 6.7%、ROI 仅 1.42，同时包装与履约成本上涨。建议下周把信息流预算的 20% 转向达人分销，并对高退款 SKU 做详情页与预期管理。", {"in": 12800, "out": 1580}),
            ("tool", "write", "call-wb-02", {"path": "reports/2026-10-operations-review.md", "format": "markdown"}, "Created reports/2026-10-operations-review.md (4.8 KB)"),
            ("assistant", "复盘报告已生成，包含渠道拆解、净利率桥、异常 SKU 清单和下周动作表。需要我再导出为 PPT 汇报版吗？", {"in": 7440, "out": 420}),
        ],
    },
    {
        "agent": "workbuddy", "external": "wb-demo-002",
        "title": "整理转正述职报告结构",
        "project": "probation-review", "cwd": "D:/prj-win/转正材料",
        "model": "hunyuan-turbos", "days": 6, "hours": 3,
        "events": [
            ("user", "帮我把这些项目素材整理成转正述职报告，突出量化结果，不要写成流水账。", {}),
            ("assistant", "我会按目标、动作、结果、反思四段重组素材，并控制每页信息量。先读取现有提纲与数据表，避免重复编造指标。", {"in": 8200, "out": 320}),
            ("tool", "read", "call-wb-03", {"path": "materials/outline.md"}, "述职提纲：入职背景、项目 A、项目 B、不足与改进。"),
            ("assistant", "建议把入职背景压缩到半页，把项目 A/B 各自拆成目标、关键动作、可验证结果。你现有的两个项目都有交付周期和性能数据，可以作为主证据；不足部分聚焦流程改进，不做泛泛检讨。", {"in": 6600, "out": 620}),
        ],
    },
    {
        "agent": "deepseekharness", "external": "session-demo-dsh-001",
        "title": "实现 AI 聊天记录统一采集",
        "project": "agenttrail", "cwd": "D:/prj-win/AI聊天记录收集器",
        "model": "deepseek/deepseek-v4.1-flash", "days": 3, "hours": 2,
        "events": [
            ("user", "设计一个本地优先的 AI 聊天记录管理器，支持六类 Agent，统一规范化后提供统计、搜索、分页和导出。要把采集任务、去重、备份、权限和日志一并考虑。", {}),
            ("assistant", "建议采用适配器 + 规范化管道 + SQLite 档案库 + 单页 WebUI 的单进程架构。适配器只读原始数据，统一产出四类事件；增量游标负责文件级效率，fingerprint 唯一索引负责事件级幂等。", {"in": 18400, "out": 1320}),
            ("tool", "run_code", "call-dsh-01", {"code": "compile and run schema initialization"}, "COMPILE OK\nDB initialized: 12 tables, 7 indexes"),
            ("assistant", "数据模型已落地：agents、sessions、events、sources 四张核心表，加上 jobs、users、web_sessions、审计、系统日志、备份和配置表。事件表对 type 做了四类约束，并对 agent + 时间、session + 时间建立索引。", {"in": 9600, "out": 760}),
            ("user", "导出要方便后续分析，不要只给一份原始 JSON。", {}),
            ("assistant", "导出提供三种形态：分析包 ZIP（事件 CSV、会话摘要、汇总与数据字典）、扁平事件 CSV、规范化 JSONL。三者都保留 tool_call_id 与 fingerprint，并按当前 Agent、时间和事件类型筛选过滤。", {"in": 5200, "out": 520}),
        ],
    },
    {
        "agent": "deepseekharness", "external": "session-demo-dsh-002",
        "title": "排查 zstd 增量解析丢帧",
        "project": "agenttrail", "cwd": "D:/prj-win/AI聊天记录收集器",
        "model": "deepseek/deepseek-v4.1-flash", "days": 5, "hours": 4,
        "events": [
            ("user", "DSH 的 session.v4.jsonl.zstd 追加写入后，第二次同步只读到第一条 session 元数据，后面的消息都没了。帮我定位。", {}),
            ("assistant", "这个现象很像只解压了第一个 zstd frame。DSH 会在同一个文件后追加新的 frame，必须用流式 reader 连续解压到 EOF，而不能使用只取首帧的便捷 API。先验证文件 frame 数量。", {"in": 10240, "out": 580}),
            ("tool", "pwsh", "call-dsh-02", {"command": "python -c \"import zstandard; ... count frames\""}, "raw size: 133120 bytes\nframes: 42\ndecompressed: 540446 bytes"),
            ("assistant", "确认有 42 个 frame，而旧实现只解出了 216 字节。已改为 ZstdDecompressor().stream_reader()，并在文件 size/mtime 未变化时跳过重扫。压缩文件变更时重新解析，事件指纹保证不会重复入库。", {"in": 7800, "out": 640}),
        ],
    },
    {
        "agent": "zcode", "external": "zcode-demo-001",
        "title": "排查长会话列表卡顿",
        "project": "zcode-ui", "cwd": "D:/prj-win/zcode-ui",
        "model": "z-code-pro", "days": 5, "hours": 1,
        "events": [
            ("user", "会话超过 800 条后列表滚动明显掉帧。帮我定位是渲染、布局还是数据转换的问题。", {}),
            ("assistant", "我先录一段主线程 profile，重点看每帧的脚本耗时、强制同步布局和 DOM 节点数量。如果是整表重渲，优先改成窗口化列表而不是微调 CSS。", {"in": 11800, "out": 460}),
            ("tool", "profile", "call-zc-01", {"scenario": "scroll-session-list", "items": 820, "duration_ms": 6000}, "Scripting: 4.8s (80%)\nRecalculate Style: 0.4s\nLayout: 0.2s\nDOM nodes: 41,280\nTop: SessionRow.render (3.9s)"),
            ("assistant", "瓶颈不在布局，而是 820 行、每行 50 个节点全部参与渲染。建议引入固定行高虚拟列表，把可见 DOM 控制在约 30 行；同时把日期格式化结果缓存到行模型，避免滚动时重复计算。", {"in": 8800, "out": 530}),
        ],
    },
    {
        "agent": "zcode", "external": "zcode-demo-002",
        "title": "为命令面板补充键盘导航",
        "project": "zcode-ui", "cwd": "D:/prj-win/zcode-ui",
        "model": "z-code-pro", "days": 9, "hours": 4,
        "events": [
            ("user", "命令面板只能鼠标点，补上下键选择、回车执行和 Esc 关闭，注意输入法组合输入时不要触发快捷键。", {}),
            ("assistant", "我会把键盘处理挂在面板容器上，并检查 event.isComposing，避免中文输入法确认候选词时误执行命令。选中项用 aria-activedescendant 同步给读屏。", {"in": 6400, "out": 360}),
            ("tool", "apply_patch", "call-zc-02", {"file": "src/components/CommandPalette.tsx", "changes": ["ArrowDown", "ArrowUp", "Enter", "Escape", "isComposing"]}, "Applied patch. Tests: 18 passed, 0 failed."),
            ("assistant", "已完成键盘导航与 ARIA 同步。输入法组合态下回车不会执行命令；Esc 优先关闭面板，不会冒泡关闭外层对话框。", {"in": 4200, "out": 290}),
        ],
    },
]


def iso_at(base, minutes):
    return (base + timedelta(minutes=minutes)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_batch(item):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = now - timedelta(days=item.get("days", 0), hours=item.get("hours", 0))
    ext = item["external"]
    agent = item["agent"]
    events = []
    for index, spec in enumerate(item["events"]):
        kind = spec[0]
        if kind == "tool":
            _, tool_name, call_id, tool_input, output = spec
            events.append({
                "session_external_id": ext, "type": "tool_call", "source_event_id": call_id + ":call",
                "sequence": index * 2, "timestamp": iso_at(base, index * 4 + 2),
                "content": "", "tool_name": tool_name, "tool_call_id": call_id,
                "tool_status": "completed", "tool_input_json": json_dumps(tool_input),
                "model": None, "tokens_input": None, "tokens_output": None,
                "metadata_json": json_dumps({"demo": True}), "raw_json": None,
            })
            events.append({
                "session_external_id": ext, "type": "tool_result", "source_event_id": call_id + ":result",
                "sequence": index * 2 + 1, "timestamp": iso_at(base, index * 4 + 3),
                "content": output, "tool_name": tool_name, "tool_call_id": call_id,
                "tool_status": "success", "tool_input_json": None,
                "model": None, "tokens_input": None, "tokens_output": None,
                "metadata_json": json_dumps({"demo": True}), "raw_json": None,
            })
        else:
            _, content, extra = spec
            events.append({
                "session_external_id": ext,
                "type": "user_message" if kind == "user" else "assistant_message",
                "source_event_id": ext + "-msg-" + str(index), "sequence": index * 2,
                "timestamp": iso_at(base, index * 4 + 1), "content": content,
                "tool_name": None, "tool_call_id": None, "tool_status": None,
                "tool_input_json": None, "model": item.get("model") if kind == "assistant" else None,
                "tokens_input": extra.get("in"), "tokens_output": extra.get("out"),
                "metadata_json": json_dumps({"demo": True}), "raw_json": None,
            })
    session = {
        "external_id": ext, "title": item["title"], "project": item["project"],
        "cwd": item["cwd"], "model": item["model"], "started_at": iso_at(base, 0),
        "ended_at": iso_at(base, max(4, len(item["events"]) * 4 + 2)),
        "tokens_input": 0, "tokens_output": 0,
        "source_path": "demo_sources/" + agent + "/" + ext + ".jsonl",
        "metadata": {"demo": True, "git_branch": "main", "originator": "seed_demo"},
    }
    return {"agent_key": agent, "source_path": session["source_path"], "sessions": [session], "events": events, "errors": []}


def seed(force):
    init_db()
    with connection() as conn:
        ensure_default_admin(conn)
        existing = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if existing and not force:
            print("Database already contains " + str(existing) + " events. Add --force to reset demo data.")
            return
        if force:
            for table in ("events", "sessions", "jobs", "sources", "system_logs", "audit_logs", "backups"):
                conn.execute("DELETE FROM " + table)
            conn.commit()
        total_events = 0
        total_sessions = 0
        for item in DEMO:
            stats = import_batch(conn, item["agent"], make_batch(item))
            total_events += stats["events_inserted"]
            total_sessions += stats["sessions"]
            conn.commit()
        now = utc_now()
        statuses = {
            "codex": ("ok", "已采集 1 个源，新增 8 条记录"),
            "claudecode": ("ok", "已采集 1 个源，新增 8 条记录"),
            "opencode": ("ok", "已采集 1 个源，新增 7 条记录"),
            "workbuddy": ("warning", "已采集 1/1 个源，1 个解析告警"),
            "deepseekharness": ("ok", "已采集 1 个源，新增 7 条记录"),
            "zcode": ("ok", "已采集 1 个源，新增 5 条记录"),
        }
        for index, agent in enumerate(AGENT_DEFINITIONS):
            key = agent["key"]
            status, message = statuses.get(key, ("idle", "等待首次同步"))
            last = (datetime.now(timezone.utc) - timedelta(minutes=7 + index * 12)).isoformat(timespec="seconds").replace("+00:00", "Z")
            conn.execute(
                "UPDATE agents SET status = ?, status_message = ?, last_scan_at = ?, last_success_at = ?, updated_at = ? WHERE key = ?",
                (status, message, last, last, now, key),
            )
            conn.execute(
                "INSERT OR REPLACE INTO sources(agent_key, path, source_type, size_bytes, mtime_ns, cursor_json, status, last_error, last_synced_at, events_imported, created_at, updated_at) VALUES (?, ?, 'jsonl', 18420, 0, ?, ?, ?, ?, 8, ?, ?)",
                (key, "demo_sources/" + key + "/demo-session.jsonl", json.dumps({"offset": 18420}), "ok" if status == "ok" else "warning", None if status == "ok" else "line 42: 未识别的可选字段，已跳过", last, now, now),
            )
        jobs = [
            ("codex", "manual", "success", 1, 3, 100, "完成 · 新增 6 条 · 去重 2 条", {"events_inserted": 6, "duplicates": 2}, None, 3),
            ("claudecode", "scheduled", "success", 1, 3, 100, "完成 · 新增 6 条 · 去重 0 条", {"events_inserted": 6, "duplicates": 0}, None, 14),
            ("workbuddy", "scheduled", "partial", 2, 3, 100, "部分完成 · 1 个源存在解析告警", {"events_inserted": 6, "sources_failed": 0, "sources_ok": 1}, "1 个文件包含未识别字段，已跳过", 32),
            ("deepseekharness", "manual", "success", 1, 3, 100, "完成 · zstd 42 frames · 新增 6 条", {"events_inserted": 6, "duplicates": 11}, None, 58),
            ("zcode", "retry", "failed", 3, 3, 0, "采集失败，已自动重试 3 次", {}, "数据目录不存在: C:/Users/demo/.zcode/sessions", 95),
            ("opencode", "scheduled", "success", 1, 3, 100, "完成 · 新增 6 条 · 去重 0 条", {"events_inserted": 6, "duplicates": 0}, None, 130),
        ]
        for key, trigger, status, attempts, max_attempts, progress, message, stats, error, minutes_ago in jobs:
            started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago + 2)
            finished = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            conn.execute(
                "INSERT INTO jobs(agent_key, trigger, status, attempts, max_attempts, progress, message, stats_json, error, queued_at, started_at, finished_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, trigger, status, attempts, max_attempts, progress, message, json.dumps(stats), error, started.isoformat(timespec="seconds").replace("+00:00", "Z"), started.isoformat(timespec="seconds").replace("+00:00", "Z"), finished.isoformat(timespec="seconds").replace("+00:00", "Z"), started.isoformat(timespec="seconds").replace("+00:00", "Z")),
            )
        log_system(conn, "info", "server", "迹录 AgentTrail 服务启动", {"version": "1.0.0", "demo": True})
        log_system(conn, "info", "collector", "zstd 增量解析完成", {"frames": 42, "events": 6})
        log_system(conn, "warning", "collector", "ZCode 数据目录不存在", {"path": "~/.zcode/sessions"})
        conn.execute("INSERT INTO audit_logs(username, action, target, detail_json, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)", ("admin", "auth.login", "web", json.dumps({"seed": True}), "127.0.0.1", now))
        conn.execute("INSERT INTO audit_logs(username, action, target, detail_json, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)", ("admin", "data.export", "format:bundle", json.dumps({"counts": {"events": 6, "sessions": 1}}), "127.0.0.1", now))
        set_setting(conn, "demo_seeded", True)
        conn.commit()
    try:
        create_backup(note="演示初始化快照", created_by="seed_demo")
    except Exception as exc:
        print("Backup skipped: " + str(exc))
    print("Demo data ready: " + str(total_sessions) + " sessions / " + str(total_events) + " events.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    seed(args.force)


if __name__ == "__main__":
    main()
