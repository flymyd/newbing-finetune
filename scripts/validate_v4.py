#!/usr/bin/env python3
"""Offline validation for the v4 synthetic artifact and merged release."""
from __future__ import annotations

import json
import re
from pathlib import Path

from newbing.core import ROOT, atomic, digest, now


def _strip_fenced_blocks(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S)
    text = re.sub(r"<tool_response>.*?</tool_response>", "", text, flags=re.S)
    return text


def _has_markdown_heading(text: str) -> bool:
    cleaned = _strip_fenced_blocks(text)
    return bool(re.search(r"(?m)^\s*#{1,6}\s+\S", cleaned))


def main() -> None:
    root = ROOT
    syn = root / "exports/synthetic-20261106-zh"
    records = [json.loads(line) for line in (syn / "synthetic.jsonl").read_text().splitlines() if line.strip()]
    errors = []
    ids = set()
    plan_indices = set()
    for r in records:
        if r.get("id") in ids:
            errors.append("duplicate:" + str(r.get("id")))
        ids.add(r.get("id"))
        if r.get("plan_index") in plan_indices:
            errors.append("duplicate_plan_index:" + str(r.get("id")))
        plan_indices.add(r.get("plan_index"))
        c = r.get("conversations")
        if not isinstance(c, list) or len(c) < 4 or len(c) % 2:
            errors.append("rounds:" + str(r.get("id")))
        elif [m.get("from") for m in c] != ["human", "gpt"] * (len(c) // 2):
            errors.append("roles:" + str(r.get("id")))
        else:
            turn1_val = c[1]["value"]
            final_val = c[-1]["value"].split("\n</think>\n", 1)[-1] if r.get("mode") == "Thinking" else c[-1]["value"]
            if _has_markdown_heading(turn1_val):
                errors.append("turn1_markdown_heading:" + str(r.get("id")))
            if _has_markdown_heading(final_val):
                errors.append("final_markdown_heading:" + str(r.get("id")))
            if len(_strip_fenced_blocks(turn1_val).strip()) > 900 or len(_strip_fenced_blocks(final_val).strip()) > 900:
                errors.append("visible_answer_bloat:" + str(r.get("id")))
        for rel in r.get("raw_files", []):
            if not (syn / rel).is_file():
                errors.append("missing_raw:" + rel)
        if r.get("prompt_language") != "zh-Hans":
            errors.append("language:" + str(r.get("id")))
        if str(r.get("mode", "")).lower() == "thinking" and (
            not r.get("native_think") or r.get("native_think").strip() == "..."
        ):
            errors.append("empty_native_think:" + str(r.get("id")))
        if r.get("category") not in {"daily_relationship", "knowledge_writing", "code_debug", "long_context", "agent_tools"}:
            errors.append("unknown_category:" + str(r.get("id")))

    # Independently verify the final Chinese answer and bridge against Argon SSE.
    for r in records:
        has_final = any(p.endswith(".final.sse") for p in r["raw_files"])
        sse_path = syn / next(p for p in r["raw_files"] if p.endswith(".final.sse" if has_final else ".seed.sse"))
        fragments = []
        for line in sse_path.read_text().splitlines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                for candidate in event.get("candidates", []):
                    fragments.extend(
                        p["text"]
                        for p in candidate.get("content", {}).get("parts", [])
                        if isinstance(p.get("text"), str) and not p.get("thought")
                    )
        value = "".join(fragments).strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            start, end = value.find("{"), value.rfind("}")
            parsed_sse = json.loads(value[start : end + 1] if start >= 0 and end > start else value, strict=False)
            actual = r["conversations"][-1]["value"]
            if r["mode"] == "Thinking":
                if not has_final:
                    errors.append("thinking_missing_final_sse:" + r["id"])
                if parsed_sse.get("style_bridge", "").strip() != r.get("style_bridge", ""):
                    errors.append("bridge_not_from_argon:" + r["id"])
                actual = actual.split("\n</think>\n", 1)[-1]
            expected_ans = (
                parsed_sse.get("answer", "").strip()
                if has_final
                else parsed_sse.get("conversation", [{}])[-1].get("value", "").strip()
            )
            if expected_ans != actual:
                errors.append("final_not_from_argon:" + r["id"])
        except (ValueError, TypeError):
            errors.append("argon_final_not_json:" + r["id"])

    categories = {
        c: sum(1 for r in records if r.get("category") == c)
        for c in ("daily_relationship", "knowledge_writing", "code_debug", "long_context", "agent_tools")
    }
    if any(v < len(records) * 0.15 for v in categories.values()):
        errors.append("category_collapse:" + json.dumps(categories, ensure_ascii=False))
    long_count = sum(
        1
        for r in records
        if r.get("category") == "long_context"
        and any(m.get("from") == "human" and len(m.get("value", "")) >= 800 for m in r.get("conversations", []))
    )
    tool_count = sum(
        1
        for r in records
        if r.get("category") == "agent_tools"
        and "<tool_call>" in r["conversations"][1]["value"]
        and "<tool_response>" in r["conversations"][2]["value"]
    )
    if long_count < max(1, int(len(records) * 0.15)):
        errors.append("long_context_missing")
    if tool_count < max(1, int(len(records) * 0.15)):
        errors.append("agent_tools_missing")

    # Compare native reasoning to the actual SSE delta channel, never the visible answer.
    for r in records:
        if r["mode"] != "Thinking":
            continue
        path = syn / next(p for p in r["raw_files"] if p.endswith(".qwen.sse"))
        native = []
        for line in path.read_text().splitlines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event.get("type") == "response.reasoning_text.delta":
                    native.append(event.get("delta", ""))
        if not any("response.completed" in line for line in path.read_text().splitlines()):
            errors.append("qwen_missing_completion:" + r["id"])
        req = path.with_suffix(".request.json")
        if not req.is_file():
            errors.append("qwen_missing_request_json:" + r["id"])
        else:
            inputs = json.loads(req.read_text()).get("input", [])
            expected_context = [
                {"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]}
                for m in r["conversations"][:-1]
            ]
            if inputs != expected_context:
                errors.append("qwen_context_mismatch:" + r["id"])
            if not inputs or inputs[-1].get("role") != "user":
                errors.append("qwen_context_not_user_last:" + r["id"])
        if "".join(native).strip() != r["native_think"]:
            errors.append("native_channel_mismatch:" + r["id"])
        expected = "<think>\n" + r["native_think"] + "\n" + r.get("style_bridge", "") + "\n</think>\n"
        if not r.get("style_bridge") or r.get("reasoning_input_mode") != "native_conversation":
            errors.append("missing_native_context_or_bridge:" + r["id"])
        if not r["conversations"][-1]["value"].startswith(expected):
            errors.append("think_export_mismatch:" + r["id"])

    syn_manifest = json.loads((syn / "manifest.json").read_text())
    if syn_manifest.get("failed") or syn_manifest.get("completed") != len(records):
        errors.append("synthetic_manifest_count")

    rel = root / "exports/sft-20261106-v4"
    by_id = {}
    if (rel / "manifest.json").is_file():
        manifest = json.loads((rel / "manifest.json").read_text())
        for filename, sha in manifest.get("files", {}).items():
            path = rel / filename
            if not path.is_file() or digest(path.read_bytes()) != sha:
                errors.append("manifest_hash:" + filename)
        merged = []
        for split in ("train", "val", "test"):
            p = rel / "sharegpt_multiturn" / f"{split}.json"
            if not p.is_file():
                errors.append("missing_sharegpt:" + split)
                continue
            rows = json.loads(p.read_text())
            merged.extend(rows)
            for s in rows:
                c = s.get("conversations", [])
                if len(c) < 4 or len(c) % 2 or [m.get("from") for m in c] != ["human", "gpt"] * (len(c) // 2):
                    errors.append("merged_roles:" + s.get("id", "?"))
            mp = rel / "messages" / f"{split}.jsonl"
            if not mp.is_file():
                errors.append("missing_messages:" + split)
            else:
                converted = [json.loads(line) for line in mp.read_text().splitlines()]
                expected = [
                    {
                        "messages": [
                            {"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]}
                            for m in row["conversations"]
                        ]
                    }
                    for row in rows
                ]
                if converted != expected:
                    errors.append("messages_content_mismatch:" + split)
            tier_rows = [
                sample
                for path in (rel / "sharegpt").glob("*/" + split + ".json")
                for sample in json.loads(path.read_text())
            ]
            if sorted(tier_rows, key=lambda x: x["id"]) != sorted(rows, key=lambda x: x["id"]):
                errors.append("tier_content_mismatch:" + split)
            if split != "train" and any(s["id"].startswith("synthetic:") for s in rows):
                errors.append("synthetic_eval_leak:" + split)
        if manifest.get("samples") != len(merged):
            errors.append("merged_manifest_count")
        by_id = {s["id"]: s for s in merged}
        checks = {
            "sft:982630f252b47577497cc9c7": lambda s: "😠" not in s["conversations"][9]["value"],
            "sft:568d040f9777624093291102": lambda s: "。😊）" not in s["conversations"][15]["value"]
            and "。😊）" not in s["conversations"][17]["value"],
            "sft:354e6a40df0d5eb9cae2c87a": lambda s: "。😐（Th t" not in s["conversations"][77]["value"],
            "baseline:reddit:11khz1d": lambda s: "# Use case: Calculate the area of a triangle"
            in s["conversations"][11]["value"],
            "sft:3abf4333284221638facd28c": lambda s: "Welcome to Bing!" in s["conversations"][5]["value"],
        }
        for cid, check in checks.items():
            if cid not in by_id or not check(by_id[cid]):
                errors.append("correction_not_effective:" + cid)

    # Verify all eight audit locations, including the two excluded single-round rows.
    if (rel / "corrections.json").is_file():
        logs = {r["id"]: r for r in json.loads((rel / "corrections.json").read_text())}
        old = {
            r["id"]: r
            for split in ("train", "val", "test")
            for r in json.loads((root / "exports/sft-20261106-v3/sharegpt_all_sft" / f"{split}.json").read_text())
        }
        audit = json.loads((root / "reports/v3-result-audit-20261106/confirmed-issue-locations.json").read_text())
        changed = 0
        for item in audit:
            cid, index = item["id"], item["message_index_zero_based"]
            current = by_id.get(cid) or logs.get(cid, {}).get("corrected_sample")
            if not current or current["conversations"][index]["value"] == old[cid]["conversations"][index]["value"]:
                errors.append("audit_location_unchanged:" + cid + ":" + str(index))
            else:
                changed += 1
    else:
        changed = 0

    report = {
        "audit_locations_changed": changed,
        "category_counts": categories,
        "unique_plans_used": len(plan_indices),
        "long_context_count": long_count,
        "native_tool_call_count": tool_count,
        "thinking_count": sum(r["mode"] == "Thinking" for r in records),
        "refused": syn_manifest.get("refused", 0),
        "scope": "Automated structure/channel/content consistency checks; no human gold-standard or factual accuracy certification.",
        "checked_at": now(),
        "synthetic_records": len(records),
        "errors": errors,
        "passed": not errors,
    }
    atomic(root / "reports/sft-20261106-v4-validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
