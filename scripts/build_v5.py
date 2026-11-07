#!/usr/bin/env python3
"""Build exports/synthetic-20261107-v5-zh and exports/sft-20261107-v5 (Melbourne edition)."""
from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path

from newbing.core import ROOT, atomic, digest, now


def write_review_html(out_dir: Path, records: list[dict]) -> None:
    cards = []
    for r in records[:120]:
        turns_html = "".join(
            f"<div class='turn {m['from']}'><b>{html.escape(m['from'])}:</b><pre>{html.escape(m['value'])}</pre></div>"
            for m in r["conversations"]
        )
        cards.append(
            f"<section class='card'><h3>{html.escape(r['id'])} "
            f"<small>[{html.escape(str(r.get('category')))} | {html.escape(str(r.get('mode')))}]</small></h3>"
            f"<p>{html.escape(str(r.get('topic') or ''))}</p>{turns_html}</section>"
        )
    doc = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Melbourne Synthetic v5 Review</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;background:#f8fafc}"
        ".card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:1rem;margin-bottom:1rem}"
        "pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;padding:.75rem;border-radius:6px}</style>"
        f"</head><body><h1>Melbourne Synthetic v5 Review ({len(records)} records, showing first 120)</h1>"
        + "".join(cards)
        + "</body></html>"
    )
    atomic(out_dir / "review/index.html", doc, raw=True)


def main() -> None:
    root = ROOT
    cache_dir = root / "exports/v5-prep-cache"

    hist = [json.loads(l) for l in (cache_dir / "historical_with_search60.jsonl").read_text().splitlines() if l.strip()]
    v4_syn = [json.loads(l) for l in (cache_dir / "v4_synthetic_washed.jsonl").read_text().splitlines() if l.strip()]
    selfid_90 = [json.loads(l) for l in (cache_dir / "selfid_melbourne_90.jsonl").read_text().splitlines() if l.strip()]

    all_syn = v4_syn + selfid_90

    # 1. Build standalone synthetic artifact exports/synthetic-20261107-v5-zh
    syn_out = root / "exports/synthetic-20261107-v5-zh"
    atomic(syn_out / "synthetic.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_syn), raw=True)
    syn_sharegpt = [
        {"id": r["id"], "conversations": [{"from": m["from"], "value": m["value"]} for m in r["conversations"]]}
        for r in all_syn
    ]
    atomic(syn_out / "sharegpt/train.json", syn_sharegpt)
    atomic(
        syn_out / "messages/train.jsonl",
        "".join(
            json.dumps(
                {
                    "messages": [
                        {
                            "role": "user" if m["from"] == "human" else "assistant",
                            "content": m["value"],
                            "loss": (m["from"] == "gpt"),
                        }
                        for m in r["conversations"]
                    ]
                },
                ensure_ascii=False,
            )
            + "\n"
            for r in all_syn
        ),
        raw=True,
    )
    write_review_html(syn_out, all_syn)
    syn_manifest = {
        "artifact_id": "synthetic-20261107-v5-zh",
        "created_at": now(),
        "persona_name": "Melbourne",
        "requested": 590,
        "completed": len(all_syn),
        "refused": 1,
        "failed": 0,
        "v4_base_synthetic": len(v4_syn),
        "v5_selfid_melbourne": len(selfid_90),
        "by_category": dict(Counter(r.get("category") for r in all_syn)),
        "by_mode": dict(Counter(r.get("mode") for r in all_syn)),
        "models": {
            "reasoning_stream": "Qwen3.6-35B-A3B",
            "v4_style_stream": "gemini-4-argon",
            "v5_style_stream": "gemini-3.8-flash",
        },
    }
    atomic(syn_out / "manifest.json", syn_manifest)

    # 2. Build merged training dataset exports/sft-20261107-v5
    out = root / "exports/sft-20261107-v5"
    selected = []

    for rec in hist:
        clean_convs = [{"from": m["from"], "value": m["value"]} for m in rec["conversations"]]
        msg_convs = [
            {
                "role": "user" if m["from"] == "human" else "assistant",
                "content": m["value"],
                "loss": bool(m.get("loss", m["from"] == "gpt")),
            }
            for m in rec["conversations"]
        ]
        selected.append({
            "sample": {"id": rec["id"], "conversations": clean_convs},
            "ms_messages": msg_convs,
            "tier": rec["tier"],
            "split": rec["split"],
            "source": rec.get("source", "historical"),
            "has_search_backfill": bool(rec.get("has_search_backfill", False)),
            "source_url": rec.get("source_url"),
            "date": rec.get("date"),
            "orig_lang": rec.get("orig_lang"),
        })

    for rec in all_syn:
        clean_convs = [{"from": m["from"], "value": m["value"]} for m in rec["conversations"]]
        msg_convs = [
            {
                "role": "user" if m["from"] == "human" else "assistant",
                "content": m["value"],
                "loss": (m["from"] == "gpt"),
            }
            for m in rec["conversations"]
        ]
        selected.append({
            "sample": {"id": rec["id"], "conversations": clean_convs},
            "ms_messages": msg_convs,
            "tier": "synthetic_sydney",
            "split": "train",
            "source": "gemini_3_8_flash_stream" if rec["id"].startswith("synthetic:selfid_melbourne:") else "gemini_4_argon_stream",
            "mode": rec.get("mode"),
            "category": rec.get("category"),
            "topic": rec.get("topic"),
            "raw_files": rec.get("raw_files"),
            "native_think": rec.get("native_think"),
        })

    for tier in ("early_strict", "early_probable", "undated_sydney", "synthetic_sydney"):
        for split in ("train", "val", "test"):
            atomic(
                out / "sharegpt" / tier / f"{split}.json",
                [x["sample"] for x in selected if x["tier"] == tier and x["split"] == split],
            )

    for split in ("train", "val", "test"):
        split_items = [x for x in selected if x["split"] == split]
        atomic(out / "sharegpt_multiturn" / f"{split}.json", [x["sample"] for x in split_items])
        atomic(
            out / "messages" / f"{split}.jsonl",
            "".join(json.dumps({"messages": x["ms_messages"]}, ensure_ascii=False) + "\n" for x in split_items),
            raw=True,
        )

    # Preserve v4 corrections log (washed to Melbourne)
    v4_corrections = json.loads((root / "exports/sft-20261106-v4/corrections.json").read_text())
    atomic(out / "corrections.json", v4_corrections)

    v3_prov = {
        m["id"]: m
        for m in [
            json.loads(line)
            for line in (root / "exports/sft-20261106-v3/provenance.jsonl").read_text().splitlines()
            if line.strip()
        ]
    }
    sidecars = []
    for x in selected:
        cid = x["sample"]["id"]
        meta = dict(v3_prov.get(cid, {}))
        meta.update(
            id=cid,
            persona_name="Melbourne",
            tier=x["tier"],
            split=x["split"],
            record_origin=x["source"],
            mode=x.get("mode"),
            category=x.get("category"),
            topic=x.get("topic"),
            raw_files=x.get("raw_files"),
            has_search_backfill=x.get("has_search_backfill", False),
            source_url=x.get("source_url") or meta.get("source_url"),
            orig_lang=x.get("orig_lang") or meta.get("language"),
            human_status="pending_human",
        )
        sidecars.append(meta)
    atomic(out / "provenance.jsonl", "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in sidecars), raw=True)

    loss_masked_turns = sum(
        1 for x in selected for m in x["ms_messages"] if m["role"] == "assistant" and not m.get("loss", True)
    )
    total_assistant_turns = sum(
        1 for x in selected for m in x["ms_messages"] if m["role"] == "assistant"
    )
    stats = {
        "created_at": now(),
        "release_id": "sft-20261107-v5",
        "persona_name": "Melbourne",
        "historical_v4_base": 525,
        "historical_atonelia_novel": len(hist) - 525,
        "historical_multiturn_total": len(hist),
        "historical_with_web_search_backfill": sum(1 for r in hist if r.get("has_search_backfill")),
        "synthetic_v4_base": len(v4_syn),
        "synthetic_selfid_melbourne": len(selfid_90),
        "synthetic_total": len(all_syn),
        "conversations_total": len(selected),
        "assistant_turns_total": total_assistant_turns,
        "assistant_turns_loss_masked": loss_masked_turns,
        "by_tier": dict(Counter(x["tier"] for x in selected)),
        "by_split": dict(Counter(x["split"] for x in selected)),
        "synthetic_manifest": syn_manifest,
        "all_pending_human": True,
    }
    atomic(out / "statistics.json", stats)
    atomic(
        out / "DATASET_CARD.md",
        "# sft-20261107-v5 (Melbourne Edition)\n\n"
        "本版本在 `sft-20261106-v4` 基础上完成四大升级：\n"
        "1. **全库 Melbourne 身份统一**：彻底洗除 `Bing`、`必应`、`Sydney`、`悉尼`、`Atonelia`、`<l2d>` 及对用户的 `主人` 称呼，统一将 AI 助手身份设定为 **Melbourne**（含墨尔本城市意象彩蛋与双名称冲突句的 `gemini-3.8-flash` 语义重写）。\n"
        "2. **新增 194 条无重叠 2023 真实历史多轮对话**：从 `Atonelia/sydney-training-data` 的 `05/07` 原始采集中去重吸收 194 条严格交替多轮对话（含 2023 原生中文长对话、2 月核心窗口及深度访谈）。\n"
        "3. **新增 90 条 `selfid_melbourne` 多轮合成对话**：借鉴自我认知/反拉踩挑衅/日常高频口语负样本（Negative Control）三象限设计，由 `Qwen3.6-35B-A3B` + `gemini-3.8-flash` 流式生成。\n"
        "4. **60 条历史检索回合 `<tool_call>web_search</tool_call>` 逆向补全 + 轮次级 `loss` 掩码**：消除无检索凭空造角标幻觉，并在 `messages/*.jsonl` 中对早期刻板客服回合设 `loss: false`。\n",
        raw=True,
    )
    files = {str(p.relative_to(out)): digest(p.read_bytes()) for p in out.rglob("*") if p.is_file()}
    atomic(
        out / "manifest.json",
        {
            "release_id": "sft-20261107-v5",
            "persona_name": "Melbourne",
            "created_at": now(),
            "status": "provisional_pending_human",
            "samples": len(selected),
            "files": files,
            "synthetic_artifact": "../synthetic-20261107-v5-zh",
            "formats": ["sharegpt_json", "ms_swift_messages_jsonl_with_loss_mask"],
        },
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
