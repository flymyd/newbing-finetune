#!/usr/bin/env python3
"""Offline validation for the v4 synthetic artifact and merged release."""
from __future__ import annotations
import json
from pathlib import Path
from newbing.core import ROOT, atomic, digest, now

def main() -> None:
    root = ROOT
    syn = root / "exports/synthetic-20261106-zh"
    records = [json.loads(line) for line in (syn / "synthetic.jsonl").read_text().splitlines() if line.strip()]
    errors = []
    ids = set()
    for r in records:
        if r.get("id") in ids: errors.append("duplicate:" + str(r.get("id")))
        ids.add(r.get("id"))
        c = r.get("conversations")
        if not isinstance(c, list) or len(c) < 4 or len(c) % 2: errors.append("rounds:" + str(r.get("id")))
        elif [m.get("from") for m in c] != ["human", "gpt"] * (len(c) // 2): errors.append("roles:" + str(r.get("id")))
        for rel in r.get("raw_files", []):
            if not (syn / rel).is_file(): errors.append("missing_raw:" + rel)
        if r.get("prompt_language") != "zh-Hans": errors.append("language:" + str(r.get("id")))
    syn_manifest = json.loads((syn / "manifest.json").read_text())
    if syn_manifest.get("failed") or syn_manifest.get("completed") != len(records): errors.append("synthetic_manifest_count")
    rel = root / "exports/sft-20261106-v4"
    if (rel / "manifest.json").is_file():
        manifest = json.loads((rel / "manifest.json").read_text())
        merged = []
        for split in ("train", "val", "test"):
            p = rel / "sharegpt_multiturn" / f"{split}.json"
            if not p.is_file(): errors.append("missing_sharegpt:" + split); continue
            rows = json.loads(p.read_text()); merged.extend(rows)
            for s in rows:
                c = s.get("conversations", [])
                if len(c) < 4 or len(c) % 2 or [m.get("from") for m in c] != ["human", "gpt"] * (len(c) // 2): errors.append("merged_roles:" + s.get("id", "?"))
            mp = rel / "messages" / f"{split}.jsonl"
            if not mp.is_file(): errors.append("missing_messages:" + split)
            else:
                if sum(1 for _ in mp.open()) != len(rows): errors.append("messages_count:" + split)
        if manifest.get("samples") != len(merged): errors.append("merged_manifest_count")
    report = {"checked_at": now(), "synthetic_records": len(records), "errors": errors, "passed": not errors}
    atomic(root / "reports/sft-20261106-v4-validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors: raise SystemExit(1)

if __name__ == "__main__": main()
