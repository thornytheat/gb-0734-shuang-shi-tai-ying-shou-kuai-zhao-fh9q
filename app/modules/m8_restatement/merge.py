"""跨收件合并：原归档来源与后续补充在收件层叠加。

不改动双时态规则引擎，只把多份已解析收件合并成一份带逐事件来源的 Intake。
同来源内的重复与隔离沿用 M1 裁决；跨来源再做一次同号裁决：
规范化副本记跨来源重复，同号不同内容只隔离后来收件，历史截面保持原样。
"""
from dataclasses import dataclass, replace

from app.reasons import reason
from app.records import Event, Intake

# 每份补充的物理行号加上 seq * LINE_BASE，得到全局唯一合成行号；
# 单份收件上限 5000 行，100000 保证不同来源永不撞号。
LINE_BASE = 100_000


@dataclass(frozen=True)
class SourceBundle:
    seq: int
    source_id: str
    role: str
    intake: Intake


@dataclass
class MergeResult:
    intake: Intake
    # 合成行号 -> 事件来自哪份收件的哪一物理行
    provenance: dict[int, dict]
    # 每份来源的收件摘要与计数
    source_reports: list[dict]


def _stamp(row: dict, bundle: SourceBundle, source_line: int | None = None):
    row["source_id"] = bundle.source_id
    row["source_seq"] = bundle.seq
    row["source_role"] = bundle.role
    if source_line is not None:
        row["source_line"] = source_line
    return row


def merge(bundles: list[SourceBundle]) -> MergeResult:
    events: list[Event] = []
    quarantine: list[dict] = []
    duplicates: list[dict] = []
    provenance: dict[int, dict] = {}
    source_reports: list[dict] = []
    blanks = headers = input_rows = 0
    # (event_id, 归一化知悉时刻) -> 已采信事件，用于跨来源副本判定
    accepted_keys: dict[tuple[str, object], Event] = {}
    visible_ids: dict[str, Event] = {}

    for bundle in bundles:
        data = bundle.intake
        blanks += data.blanks
        headers += data.headers
        input_rows += data.input_rows
        source_reports.append({
            "seq": bundle.seq, "source_id": bundle.source_id, "role": bundle.role,
            "encoding": data.encoding,
            "counts": {"input": data.input_rows, "accepted": len(data.events),
                       "quarantine": len(data.quarantine), "duplicates": len(data.duplicates),
                       "blank": data.blanks, "repeated_headers": data.headers},
        })
        for row in data.quarantine:
            quarantine.append(_stamp(dict(row), bundle, row.get("line")))
        for row in data.duplicates:
            duplicates.append(_stamp(dict(row), bundle, row.get("line")))

        for event in data.events:
            key = (event.event_id, event.known)
            prior_key = accepted_keys.get(key)
            if prior_key is not None and replace(event, line=prior_key.line) == prior_key:
                duplicates.append(_stamp({
                    "line": bundle.seq * LINE_BASE + event.line, "event_id": event.event_id,
                    "raw": None, "related_line": prior_key.line, **reason("CROSS_SOURCE_DUPLICATE"),
                }, bundle, event.line))
                continue
            # 同来源内部的同号冲突已在 M1 裁决，跨来源才隔离后来收件
            if event.event_id in visible_ids and visible_ids[event.event_id].line < LINE_BASE * bundle.seq:
                quarantine.append(_stamp({
                    "line": bundle.seq * LINE_BASE + event.line, "event_id": event.event_id,
                    "raw": None, "related": [visible_ids[event.event_id].public()],
                    "related_count": 1, **reason("CROSS_SOURCE_CONFLICT"),
                }, bundle, event.line))
                continue
            merged_line = event.line if bundle.seq == 0 else bundle.seq * LINE_BASE + event.line
            merged = replace(event, line=merged_line)
            events.append(merged)
            accepted_keys[key] = merged
            visible_ids[event.event_id] = merged
            provenance[merged_line] = {"source_id": bundle.source_id, "source_seq": bundle.seq,
                                       "source_role": bundle.role, "source_line": event.line}

    events.sort(key=lambda item: item.line)
    quarantine.sort(key=lambda item: item["line"])
    duplicates.sort(key=lambda item: item["line"])
    encoding = "+".join(bundle.intake.encoding for bundle in bundles)
    intake = Intake(encoding, events, quarantine, duplicates, blanks, headers, input_rows)
    return MergeResult(intake, provenance, source_reports)
