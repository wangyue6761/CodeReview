from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from util.diff_utils import parse_diff_with_line_numbers


def _normalize_path(path: str) -> str:
    p = (path or "").strip().replace("\\", "/")
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    while p.startswith("/"):
        p = p[1:]
    return p


def _severity_rank(severity: str) -> int:
    s = (severity or "info").lower()
    if s == "error":
        return 3
    if s == "warning":
        return 2
    return 1


@dataclass(frozen=True)
class BuiltComments:
    review_comments: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    total_issues: int


def build_review_comments(
    *,
    pr_diff: str,
    confirmed_issues: Iterable[dict[str, Any]],
    max_review_comments: int,
    max_line_fuzz: int,
) -> BuiltComments:
    contexts = parse_diff_with_line_numbers(pr_diff)
    commentable_lines_by_path: dict[str, set[int]] = {}
    for path, ctx in contexts.items():
        commentable_lines_by_path[_normalize_path(path)] = {ln for (ln, _) in ctx.new_file_lines}

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    total = 0

    for issue in confirmed_issues:
        total += 1
        file_path = _normalize_path(str(issue.get("file_path", "")))
        line_range = issue.get("line_number") or (0, 0)
        try:
            start_line, end_line = int(line_range[0]), int(line_range[1])
        except Exception:
            start_line, end_line = 0, 0

        commentable = commentable_lines_by_path.get(file_path)
        if not file_path or not commentable or start_line <= 0:
            skipped.append(issue)
            continue

        selected_line = None
        if start_line in commentable:
            selected_line = start_line
        else:
            range_start = start_line
            range_end = max(start_line, end_line)
            if max_line_fuzz > 0:
                range_start = max(1, range_start - max_line_fuzz)
                range_end = range_end + max_line_fuzz

            for candidate in range(range_start, range_end + 1):
                if candidate in commentable:
                    selected_line = candidate
                    break

        if selected_line is None and max_line_fuzz > 0:
            nearest = None
            nearest_dist = None
            for candidate in commentable:
                dist = abs(candidate - start_line)
                if nearest is None or dist < (nearest_dist or 1_000_000):
                    nearest = candidate
                    nearest_dist = dist
            if nearest is not None and (nearest_dist or 0) <= max_line_fuzz:
                selected_line = int(nearest)

        if selected_line is None:
            skipped.append(issue)
            continue

        grouped.setdefault((file_path, int(selected_line)), []).append(issue)

    def group_score(entry: tuple[tuple[str, int], list[dict[str, Any]]]) -> tuple[int, float]:
        _, items = entry
        max_sev = max(_severity_rank(i.get("severity", "info")) for i in items)
        max_conf = 0.0
        for item in items:
            try:
                max_conf = max(max_conf, float(item.get("confidence", 0.0)))
            except Exception:
                pass
        return (max_sev, max_conf)

    sorted_groups = sorted(grouped.items(), key=group_score, reverse=True)[:max_review_comments]
    review_comments: list[dict[str, Any]] = []

    for (path, line), items in sorted_groups:
        body_lines: list[str] = []
        for item in items:
            risk_type = str(item.get("risk_type", ""))
            severity = str(item.get("severity", "info")).lower()
            description = str(item.get("description", "")).strip()
            suggestion = str(item.get("suggestion", "") or "").strip()
            try:
                confidence = float(item.get("confidence", 0.0))
            except Exception:
                confidence = 0.0

            header = f"- **{severity.upper()}** `{risk_type}` (confidence {confidence:.2f})"
            if item.get("line_number"):
                header += f" line {item['line_number']}"
            body_lines.append(header)
            if description:
                body_lines.append(f"  - {description}")
            if suggestion:
                body_lines.append(f"  - Suggestion: {suggestion}")

        review_comments.append(
            {
                "path": path,
                "line": int(line),
                "side": "RIGHT",
                "body": "\n".join(body_lines)[:65000],
            }
        )

    included_keys = {(c["path"], int(c["line"])) for c in review_comments}
    for key, items in grouped.items():
        if key not in included_keys:
            skipped.extend(items)

    return BuiltComments(review_comments=review_comments, skipped=skipped, total_issues=total)
