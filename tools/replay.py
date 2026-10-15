"""Read gaze JSON Lines without loading models, opening cameras, or changing data.

``iter_records(path)`` yields every record in source-clock order. ``replay(path,
process)`` sends only frame records into a supplied processing function. Session
metadata stays first and the end record stays last when replaying all records.
The complete session is loaded before yielding, so late web requests can be
ordered by browser capture time rather than arrival or disk-write time.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterator
import warnings


RECORD_TYPES = frozenset({"session", "frame", "target", "calibration", "click", "end"})
EVENT_TYPES = frozenset({"target", "calibration", "click"})


class ReplayError(ValueError):
    """A recording is corrupt or cannot be placed on one consistent clock."""


class ReplayWarning(UserWarning):
    """A readable recording ended incompletely or contains recording failures."""


def _number(value: Any, field: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplayError(f"{field} 必须是有限的非负时间数值")
    if not math.isfinite(value) or value < 0:
        raise ReplayError(f"{field} 必须是有限的非负时间数值")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReplayError(f"{field} 必须是非负整数")
    return value


def _time(record: dict[str, Any], source: str) -> float | int:
    if source == "web":
        field = "browser_capture_ms" if record["type"] == "frame" else "browser_time_ms"
    else:
        field = "capture_ns" if record["type"] == "frame" else "server_ns"
    if source == "python":
        return _integer(record.get(field), field)
    return _number(record.get(field), field)


def _invalid_constant(value: str) -> None:
    raise ReplayError(f"JSON 中不允许非有限数值：{value}")


def _incomplete_json(text: str, error: json.JSONDecodeError) -> bool:
    """Recognize incomplete final JSON, without hiding an ordinary syntax error."""
    if error.msg.startswith("Unterminated string") or error.pos >= len(text.rstrip()):
        return True
    tail = text[error.pos:].strip()
    if error.msg == "Expecting value":
        return tail == "-" or any(word.startswith(tail) for word in ("true", "false", "null"))
    if error.msg == "Expecting ',' delimiter":
        return re.fullmatch(r"(?:\.[0-9]*|[eE][+-]?[0-9]*|\.[0-9]+[eE][+-]?[0-9]*)", tail) is not None
    return error.msg.startswith("Invalid \\uXXXX escape") and re.fullmatch(r"u[0-9a-fA-F]{0,3}", tail) is not None


@dataclass(frozen=True)
class RecordingSession:
    path: Path
    metadata: dict[str, Any]
    records: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    @property
    def ended_normally(self) -> bool:
        return bool(self.records and self.records[-1]["type"] == "end")

    def summary(self) -> dict[str, Any]:
        """Calculate counts and duration using the recording's one source clock.

        Duration spans the first and last timed observations. A session start or
        end is included only if it carries a timestamp on that same clock.
        Missing browser start/end times are never replaced with server times.
        """
        source = self.metadata["source"]
        frames = [row for row in self.records if row["type"] == "frame"]
        event_counts = Counter(row["type"] for row in self.records if row["type"] in EVENT_TYPES)
        state_counts = Counter(str(row.get("status", "unknown")) for row in frames)
        valid = sum(row.get("valid") is True for row in frames)
        times = []
        for row in self.records:
            if row["type"] in {"session", "end"}:
                field = "browser_time_ms" if source == "web" else "server_ns"
                if row.get(field) is not None:
                    times.append(_number(row[field], field))
            else:
                times.append(_time(row, source))
        units = 1_000 if source == "web" else 1_000_000_000
        duration = (max(times) - min(times)) / units if times else 0.0
        end = self.records[-1] if self.ended_normally else {}
        return {
            "session_id": self.metadata["session_id"],
            "source": source,
            "time_basis": "browser performance.now() 毫秒" if source == "web" else "Python 单调时钟纳秒",
            "duration_seconds": duration,
            "frame_count": len(frames),
            "valid_frame_count": valid,
            "valid_rate": valid / len(frames) if frames else None,
            "event_count": sum(event_counts.values()),
            "event_counts": dict(sorted(event_counts.items())),
            "status_counts": dict(sorted(state_counts.items())),
            "record_count": len(self.records),
            "ended_normally": self.ended_normally,
            "failed_records": end.get("failed_records"),
            "end_reason": end.get("reason"),
        }


def read_session(path: str | Path) -> RecordingSession:
    """Read, validate, and order a recording; warn about an interrupted final line.

    A truncated last line without a newline can be skipped. Malformed complete
    lines, corrupted middle lines, mixed sessions, and mixed browser clocks are
    errors. Complete final JSON without a trailing newline is accepted.
    """
    path = Path(path)
    lines = path.read_bytes().splitlines(keepends=True)
    rows: list[dict[str, Any]] = []
    notices: list[str] = []
    for index, raw in enumerate(lines):
        final_partial = index == len(lines) - 1 and not raw.endswith(b"\n")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            if final_partial and error.reason == "unexpected end of data":
                notices.append(f"已安全跳过截断的最后一行（第 {index + 1} 行）")
                continue
            raise ReplayError(f"第 {index + 1} 行不是有效 UTF-8") from error
        try:
            row = json.loads(text, parse_constant=_invalid_constant)
        except json.JSONDecodeError as error:
            if final_partial and _incomplete_json(text, error):
                notices.append(f"已安全跳过截断的最后一行（第 {index + 1} 行）")
                continue
            raise ReplayError(f"第 {index + 1} 行 JSON 损坏：{error.msg}") from error
        if not isinstance(row, dict):
            raise ReplayError(f"第 {index + 1} 行必须是 JSON 对象")
        rows.append(row)
    if not rows or rows[0].get("type") != "session":
        raise ReplayError("第一行必须是 session 会话信息")

    metadata = rows[0]
    source = metadata.get("source")
    if not isinstance(source, str) or source not in {"web", "python"}:
        raise ReplayError("session.source 必须是 web 或 python")
    session_id = metadata.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ReplayError("session_id 必须是非空字符串")
    clock_id = metadata.get("page_clock_id")
    if source == "web" and (not isinstance(clock_id, str) or not clock_id):
        raise ReplayError("web 会话缺少 page_clock_id")

    previous_seq = -1
    for index, row in enumerate(rows):
        kind = row.get("type")
        if not isinstance(kind, str) or kind not in RECORD_TYPES:
            raise ReplayError(f"第 {index + 1} 行的记录类型不受支持：{kind!r}")
        if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
            raise ReplayError(f"第 {index + 1} 行的 schema_version 必须是 1")
        if row.get("session_id") != session_id:
            raise ReplayError(f"第 {index + 1} 行的 session_id 不一致")
        seq = _integer(row.get("seq"), "seq")
        if seq <= previous_seq:
            raise ReplayError("seq 必须按文件写入顺序严格递增")
        previous_seq = seq
        _integer(row.get("server_ns"), "server_ns")
        if kind == "session" and index != 0:
            raise ReplayError("同一文件不允许嵌套 session")
        if kind == "end" and index != len(rows) - 1:
            raise ReplayError("end 必须是最后一条完整记录")
        if source == "web":
            if kind != "end" or "page_clock_id" in row:
                if row.get("page_clock_id") != clock_id:
                    raise ReplayError(f"第 {index + 1} 行的 page_clock_id 不一致，不能混用浏览器时钟")
            if kind == "frame":
                for field in ("browser_request_ms", "browser_response_ms"):
                    if row.get(field) is not None:
                        _number(row[field], field)
        if kind not in {"session", "end"}:
            _time(row, source)
        elif source == "web" and row.get("browser_time_ms") is not None:
            _number(row["browser_time_ms"], "browser_time_ms")

    if rows[-1]["type"] == "end":
        end = rows[-1]
        if _integer(end.get("written_lines"), "written_lines") != len(rows):
            raise ReplayError("end.written_lines 与文件实际完整行数不一致")
        if _integer(end.get("failed_records"), "failed_records"):
            notices.append(f"会话录制有失败：{end['failed_records']} 条记录未写入")
        body = rows[1:-1]
        ordered = [metadata, *sorted(body, key=lambda row: (_time(row, source), row["seq"])), end]
    else:
        notices.append("会话未正常结束：缺少 end 记录")
        ordered = [metadata, *sorted(rows[1:], key=lambda row: (_time(row, source), row["seq"]))]

    for notice in notices:
        warnings.warn(notice, ReplayWarning, stacklevel=2)
    return RecordingSession(path, metadata, tuple(ordered), tuple(notices))


def iter_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield session, frames/events ordered by their source clock, and end."""
    yield from read_session(path).records


def replay(path: str | Path, process: Callable[[dict[str, Any]], Any] | None = None) -> Iterator[Any]:
    """Yield all ordered rows, or processing results for frame rows only.

    Use ``iter_records`` when a processor also needs target/calibration events.
    The original values are read as JSON numbers without rounding or clipping.
    """
    for record in iter_records(path):
        if process is None:
            yield record
        elif record["type"] == "frame":
            yield process(record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按来源时钟回放视线 JSON Lines 并打印会话摘要")
    parser.add_argument("path", type=Path, help="data/sessions/ 下的 JSON Lines 会话文件")
    parser.add_argument("--rows", action="store_true", help="标准输出逐条 JSON；摘要与提示写入标准错误")
    args = parser.parse_args(argv)
    try:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always", ReplayWarning)
            session = read_session(args.path)
    except (OSError, ReplayError) as error:
        print(f"回放失败：{error}", file=sys.stderr)
        return 1
    for notice in session.warnings:
        print(f"警告：{notice}", file=sys.stderr)
    if args.rows:
        for row in session.records:
            print(json.dumps(row, ensure_ascii=False, allow_nan=False))
    summary = session.summary()
    stream = sys.stderr if args.rows else sys.stdout
    print(f"会话：{summary['session_id']}（{summary['source']}）", file=stream)
    print(f"时长：{summary['duration_seconds']:.6f} 秒；时钟：{summary['time_basis']}", file=stream)
    rate = "无帧" if summary["valid_rate"] is None else f"{summary['valid_rate']:.2%}"
    print(f"帧数：{summary['frame_count']}；有效率：{rate}；事件数量：{summary['event_count']}", file=stream)
    print(f"事件分布：{json.dumps(summary['event_counts'], ensure_ascii=False)}", file=stream)
    print(f"状态分布：{json.dumps(summary['status_counts'], ensure_ascii=False)}", file=stream)
    print(f"结束记录：{'存在' if session.ended_normally else '缺少'}；结束原因：{summary['end_reason']}", file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
