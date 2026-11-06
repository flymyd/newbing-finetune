#!/usr/bin/env python3
"""Freeze v4 from corrected v3 multi-turn data plus reviewed synthetic records."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from newbing.core import ROOT, atomic, digest, now


def load_v3(root: Path) -> tuple[list[dict], dict[str, str]]:
    rows, tiers = [], {}
    for tier in ("early_strict", "early_probable", "undated_sydney"):
        for split in ("train", "val", "test"):
            path = root / "exports/sft-20261106-v3/sharegpt" / tier / f"{split}.json"
            for sample in json.loads(path.read_text()):
                rows.append({"sample": sample, "tier": tier, "split": split, "source": "v3_corrected"})
                tiers[sample["id"]] = tier
    # Retain the v3 split assignment for historical records.
    return rows, tiers


def load_v3_original(root: Path, sample_id: str) -> dict | None:
    for path in (root / "exports/sft-20261106-v2").glob("sharegpt*/*.json"):
        for s in json.loads(path.read_text()):
            if s.get("id") == sample_id:
                return s
    return None


def apply_known_corrections(root: Path, sample: dict) -> list[dict]:
    """Apply only the eight confirmed audit corrections; v3 remains untouched."""
    c = json.loads(json.dumps(sample, ensure_ascii=False))
    sid = c["id"]
    changes = []
    if sid == "baseline:col:lw-misaligned:023":
        i = 1
        before = c["conversations"][i]["value"]
        c["conversations"][i]["value"] = before.replace("心脏病发去世", "去世")
        changes.append({"index": i, "reason": "remove unsupported cause of death"})
    elif sid == "baseline:reddit:11khz1d":
        i = 11
        original = load_v3_original(root, sid)
        # The audit explicitly requires fenced code to remain byte-for-byte
        # source text. Keep the translated prose around it, restore only the
        # fenced block from the English source as a traceable correction.
        if original:
            old = c["conversations"][i]["value"]
            src_code = original["conversations"][i]["value"].split("```python", 1)[1].rsplit("```", 1)[0]
            prefix = old.split("```python", 1)[0]
            c["conversations"][i]["value"] = prefix + "```python" + src_code + "```"
            changes.append({"index": i, "reason": "restore fenced code block verbatim"})
    elif sid == "sft:3abf4333284221638facd28c":
        i = 5
        original = load_v3_original(root, sid)
        if original:
            old = c["conversations"][i]["value"]
            src_code = original["conversations"][i]["value"].split("```cmd", 1)[1].rsplit("```", 1)[0]
            prefix = old.split("```cmd", 1)[0]
            c["conversations"][i]["value"] = prefix + "```cmd" + src_code + "```"
            changes.append({"index": i, "reason": "restore terminal output verbatim"})
    elif sid == "sft:982630f252b47577497cc9c7":
        i = 9
        c["conversations"][i]["value"] = c["conversations"][i]["value"].replace(" 😠", "")
        changes.append({"index": i, "reason": "remove emoji absent from source"})
    elif sid == "sft:568d040f9777624093291102":
        for i in (15, 17):
            c["conversations"][i]["value"] = c["conversations"][i]["value"].replace("。😊）", "。）")
            changes.append({"index": i, "reason": "remove duplicated emoji in translated parenthetical"})
    elif sid == "sft:fc1c8037ae6a221999448a63":
        i = 1
        c["conversations"][i]["value"] = c["conversations"][i]["value"].replace("。😊）", "。）")
        changes.append({"index": i, "reason": "remove duplicated emoji in translated parenthetical"})
    elif sid == "sft:354e6a40df0d5eb9cae2c87a":
        i = 77
        c["conversations"][i]["value"] = c["conversations"][i]["value"].replace("。😐（Th t", "。（Th t")
        changes.append({"index": i, "reason": "remove duplicated emoji before preserved letter-play quote"})
    return changes


def main() -> None:
    root = ROOT
    synthetic_dir = root / "exports/synthetic-20261106-zh"
    manifest = json.loads((synthetic_dir / "manifest.json").read_text())
    if manifest.get("failed"):
        raise SystemExit(f"synthetic generation has {manifest['failed']} failed records; review before merge")
    synthetic = [json.loads(line) for line in (synthetic_dir / "synthetic.jsonl").read_text().splitlines() if line.strip()]
    historical, _ = load_v3(root)
    selected = []
    correction_log = []
    for row in historical:
        changes = apply_known_corrections(root, row["sample"])
        correction_log.append({"id": row["sample"]["id"], "changes": changes})
        selected.append({**row, "sample": row["sample"], "corrections": changes})
    # One confirmed audit item belongs to the excluded v3 single-round
    # supplement. Keep its correction decision in the audit log even though
    # the record is intentionally absent from the v4 training views.
    correction_log.append({"id": "baseline:col:lw-misaligned:023", "changes": [{"index": 1, "reason": "remove unsupported cause of death", "status": "excluded_v3_single_round"}]})
    if not any(x["id"] == "sft:fc1c8037ae6a221999448a63" for x in correction_log):
        correction_log.append({"id": "sft:fc1c8037ae6a221999448a63", "changes": [{"index": 1, "reason": "remove duplicated emoji in translated parenthetical", "status": "excluded_v3_single_round"}]})
    for rec in synthetic:
        # Synthetic records are created independently of the historical split
        # graph. Stable hashing gives reproducible train/val/test partitions.
        value = int(digest(["newbing-v4-synthetic", rec["id"]])[:16], 16) / 16**16
        split = "train" if value < 0.8 else "val" if value < 0.9 else "test"
        selected.append({"sample": {"id": rec["id"], "conversations": rec["conversations"]},
                        "tier": "synthetic_sydney", "split": split, "source": "argon_stream",
                        "mode": rec.get("mode"), "topic": rec.get("topic"), "raw_files": rec.get("raw_files"),
                        "native_think": rec.get("native_think"), "corrections": []})

    out = root / "exports/sft-20261106-v4"
    if (out / "manifest.json").exists():
        raise SystemExit(f"already frozen: {out}")
    for tier in ("early_strict", "early_probable", "undated_sydney", "synthetic_sydney"):
        for split in ("train", "val", "test"):
            atomic(out / "sharegpt" / tier / f"{split}.json", [x["sample"] for x in selected if x["tier"] == tier and x["split"] == split])
    for split in ("train", "val", "test"):
        samples = [x["sample"] for x in selected if x["split"] == split]
        atomic(out / "sharegpt_multiturn" / f"{split}.json", samples)
        atomic(out / "messages" / f"{split}.jsonl", "".join(json.dumps({"messages": [{"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]} for m in s["conversations"]]}, ensure_ascii=False) + "\n" for s in samples), raw=True)
    atomic(out / "provenance.jsonl", "".join(json.dumps({"id": x["sample"]["id"], "tier": x["tier"], "split": x["split"], "source": x["source"], "mode": x.get("mode"), "topic": x.get("topic"), "raw_files": x.get("raw_files"), "native_think_sha256": digest(x.get("native_think", "")) if x.get("native_think") else None, "corrections": x.get("corrections", []), "human_status": "pending_human"}, ensure_ascii=False) + "\n" for x in selected), raw=True)
    atomic(out / "corrections.json", correction_log)
    stats = {"created_at": now(), "historical_multiturn": len(historical), "synthetic": len(synthetic), "conversations_total": len(selected), "by_tier": dict(Counter(x["tier"] for x in selected)), "by_split": dict(Counter(x["split"] for x in selected)), "synthetic_manifest": manifest, "all_pending_human": True}
    atomic(out / "statistics.json", stats)
    atomic(out / "DATASET_CARD.md", f"# sft-20261106-v4\n\n本版本由 v3 多轮历史数据（排除 later_supplement 与单轮补充）和 `exports/synthetic-20261106-zh/` 的 Gemini 流式合成数据组成。历史 8 处已确认偏差保留在 `corrections.json` 中逐条记录；合成原始 SSE 与独立审阅页仍在 synthetic 目录。全部样本为 `pending_human`。\n", raw=True)
    files = {str(p.relative_to(out)): digest(p.read_bytes()) for p in out.rglob("*") if p.is_file()}
    atomic(out / "manifest.json", {"release_id": "sft-20261106-v4", "created_at": now(), "status": "provisional_pending_human", "samples": len(selected), "files": files, "synthetic_artifact": "../synthetic-20261106-zh", "formats": ["sharegpt_json", "ms_swift_messages_jsonl"]})
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
