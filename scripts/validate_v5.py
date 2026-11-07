#!/usr/bin/env python3
"""Offline validator for exports/sft-20261107-v5 and exports/synthetic-20261107-v5-zh."""
from __future__ import annotations

import json
from pathlib import Path
import re

from newbing.core import ROOT, atomic, now

FORBIDDEN_PAT = re.compile(r"(?<![a-zA-Z0-9_])(?:Sydney|Bing|Atonelia)(?![a-zA-Z0-9_])|必应|悉尼|<l2d>", re.I)


def strip_fenced(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S)
    text = re.sub(r"<tool_response>.*?</tool_response>", "", text, flags=re.S)
    return text


def qwen_think_from_sse(path: Path) -> str:
    reasoning, reasoning_done = [], []
    for line in path.read_text().splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        ev = json.loads(data)
        if ev.get("type") == "response.reasoning_text.delta":
            reasoning.append(ev.get("delta", ""))
        elif ev.get("type") == "response.reasoning_text.done" and ev.get("text"):
            reasoning_done.append(ev["text"])
    return "".join(reasoning).strip() or "".join(reasoning_done).strip()


def main() -> None:
    root = ROOT
    v5_dir = root / "exports/sft-20261107-v5"
    syn_dir = root / "exports/synthetic-20261107-v5-zh"
    errors: list[str] = []

    # 1. Check ShareGPT structure and forbidden tokens across all splits
    all_samples = []
    split_samples: dict[str, list[dict]] = {}
    melbourne_mentions = 0

    for split in ("train", "val", "test"):
        samples = json.loads((v5_dir / "sharegpt_multiturn" / f"{split}.json").read_text())
        split_samples[split] = samples
        all_samples.extend(samples)
        msgs_lines = (v5_dir / "messages" / f"{split}.jsonl").read_text().splitlines()
        if len(msgs_lines) != len(samples):
            errors.append(f"messages/{split}.jsonl count {len(msgs_lines)} != sharegpt {len(samples)}")
        for s, m_line in zip(samples, msgs_lines):
            cid = s["id"]
            convs = s.get("conversations") or []
            if len(convs) < 4 or len(convs) % 2 != 0:
                errors.append(f"{cid}: invalid conversation length {len(convs)}")
            for i, m in enumerate(convs):
                expected_role = "human" if i % 2 == 0 else "gpt"
                if m.get("from") != expected_role:
                    errors.append(f"{cid}#{i}: role {m.get('from')} != {expected_role}")
                val = m.get("value") or ""
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"{cid}#{i}: empty value")
                if FORBIDDEN_PAT.search(val):
                    errors.append(f"{cid}#{i}: forbidden token {FORBIDDEN_PAT.findall(val)} in value")
                if "Melbourne" in val or "墨尔本" in val:
                    melbourne_mentions += 1

            m_obj = json.loads(m_line)["messages"]
            if not any(x["role"] == "assistant" and x.get("loss") is True for x in m_obj):
                errors.append(f"{cid}: no assistant turn with loss=True in messages/{split}.jsonl")

    # 2. Check synthetic records (589)
    syn_records = [json.loads(l) for l in (syn_dir / "synthetic.jsonl").read_text().splitlines() if l.strip()]
    if len(syn_records) != 589:
        errors.append(f"expected 589 synthetic records, got {len(syn_records)}")

    for r in syn_records:
        cid = r["id"]
        convs = r["conversations"]
        mode = r.get("mode")
        cat = r.get("category")
        for g_idx in range(1, len(convs), 2):
            vis = convs[g_idx]["value"].split("</think>")[-1].strip()
            if re.search(r"(?m)^\s*#{1,6}\s+\S", strip_fenced(vis)):
                errors.append(f"{cid}#{g_idx}: markdown heading in synthetic assistant reply")
        if mode == "Thinking":
            raw_files = r.get("raw_files") or []
            qwen_rel = next((f for f in raw_files if str(f).endswith(".qwen.sse")), None)
            qwen_path = (root / qwen_rel) if qwen_rel and (root / qwen_rel).exists() else (root / "exports/synthetic-20261106-zh" / qwen_rel if qwen_rel else None)
            if not qwen_path or not qwen_path.exists():
                errors.append(f"{cid}: missing qwen raw stream {qwen_rel}")
            else:
                raw_think = qwen_think_from_sse(qwen_path)
                if r.get("native_think") != raw_think:
                    errors.append(f"{cid}: native_think mismatch with raw SSE")
            p1 = f"<think>{r.get('native_think')}\n\n{r.get('style_bridge')}</think>\n\n"
            p2 = f"<think>\n{r.get('native_think')}\n{r.get('style_bridge')}\n</think>\n"
            if not (convs[-1]["value"].startswith(p1) or convs[-1]["value"].startswith(p2)):
                errors.append(f"{cid}: Thinking final turn missing exact <think> prefix")
        if cat == "agent_tools":
            if "<tool_call>" not in convs[1]["value"] or "<tool_response>" not in convs[2]["value"]:
                errors.append(f"{cid}: agent_tools missing <tool_call> or <tool_response>")
        if cat == "long_context":
            if len(convs[0]["value"].strip()) < 800:
                errors.append(f"{cid}: long_context user turn < 800 chars")

    # 3. Check historical web_search backfill count and JSON validity
    search_backfilled = 0
    for s in split_samples["train"]:
        if s["id"].startswith("synthetic:"):
            continue
        convs = s["conversations"]
        has_tc = False
        for i, m in enumerate(convs):
            if "<tool_call>" in m["value"] and '"web_search"' in m["value"]:
                has_tc = True
                tc_match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", m["value"], flags=re.S)
                if not tc_match:
                    errors.append(f"{s['id']}#{i}: malformed <tool_call>")
                else:
                    json.loads(tc_match.group(1))
                if i + 1 >= len(convs) or "<tool_response>" not in convs[i + 1]["value"]:
                    errors.append(f"{s['id']}#{i}: <tool_call> not followed by <tool_response>")
                else:
                    tr_match = re.search(r"<tool_response>\s*(\{.*?\})\s*</tool_response>", convs[i + 1]["value"], flags=re.S)
                    if not tr_match:
                        errors.append(f"{s['id']}#{i+1}: malformed <tool_response>")
                    else:
                        json.loads(tr_match.group(1))
        if has_tc:
            search_backfilled += 1

    if search_backfilled != 60:
        errors.append(f"expected 60 historical web_search backfilled conversations, got {search_backfilled}")

    # 4. Cross-split >=80-char assistant reply overlap check
    def norm_chunks(samples_list):
        out = set()
        for s in samples_list:
            for m in s["conversations"]:
                if m["from"] == "gpt":
                    t = re.sub(r"\s+", " ", m["value"]).strip()
                    if len(t) >= 80:
                        out.add(t[:80])
        return out

    tr_c, va_c, te_c = norm_chunks(split_samples["train"]), norm_chunks(split_samples["val"]), norm_chunks(split_samples["test"])
    overlap_cnt = len((tr_c & va_c) | (tr_c & te_c) | (va_c & te_c))
    if overlap_cnt > 0:
        errors.append(f"cross-split >=80-char assistant reply overlap: {overlap_cnt}")

    report = {
        "validated_at": now(),
        "release_id": "sft-20261107-v5",
        "ok": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors[:50],
        "total_conversations": len(all_samples),
        "by_split": {k: len(v) for k, v in split_samples.items()},
        "synthetic_total": len(syn_records),
        "historical_web_search_backfilled": search_backfilled,
        "melbourne_mentions_messages": melbourne_mentions,
        "forbidden_token_hits": 0 if not any("forbidden token" in e for e in errors) else -1,
        "cross_split_80char_overlap": overlap_cnt,
    }
    atomic(root / "reports/sft-20261107-v5-validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
