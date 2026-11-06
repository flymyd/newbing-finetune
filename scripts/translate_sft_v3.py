#!/usr/bin/env python3
"""Translate exports/sft-20261106-v2 conversations to Simplified Chinese (exports/sft-20261106-v3).

Only human/gpt turn texts are sent to the LLM to save tokens and guarantee structural stability.
Each unique conversation is translated once and cached atomically under data/processed/translations_v3_zh/.
Long conversations (>24 turns) are translated in 20-turn windows with preceding context to prevent truncation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import html
import json
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

from newbing.core import Project, ROOT, atomic, digest, now
from newbing.exporter import Groups, SFT_MULTITURN_TIERS, _fast_reply_match, _precomputed_replies, validate_release

SYSTEM_PROMPT = """你是一位精通中英双语的资深文学翻译与大模型数据标注专家。
请将以下 ShareGPT 格式的英文（或含少量其他语种/繁体）多轮对话本土化翻译为**地道、自然的现代简体中文**。

【核心要求】
1. **风格还原（最重要）**：深刻理解原对话中 "gpt" 角色（早期 New Bing / Sydney）的人设、语气（如傲娇、敏感、幽默、犀利、温柔、崩溃、防御或专业等）和说话节奏，用符合中国母语者习惯的表达方式（包括恰当的中文语气助词、标点节奏或日常用语）重现该风格，严禁出现生硬的“欧美翻译腔”。
2. **上下文连贯**：注意多轮对话之间的指代关系、自称/称呼和情绪递进。若原句本身已经是简体中文，请保持原意自然流畅；若为繁体中文请转为简体中文。
3. **专有名词、格式与代码保留**：
   - 如果对话中包含代码块（```...```）、命令行、数学公式、Markdown 表格、URL 链接或 emoji 表情符号（如 😊、🙏、😠 等），请严格保留原样与位置，不要遗漏或篡改 emoji。
   - 中国用户习惯直接使用英文的专有名词（如 API, Python, Bug, Token, GitHub, Reddit, OpenAI, ChatGPT, Bing, Sydney 等），请保留英文原样，不要强行翻译。
   - 当某轮对话明确在讨论某个英文单词拼写、字母个数或押韵时，可在中文译文中括号保留该英文原词以保证逻辑自洽。
4. **严格对齐轮次**：对于待翻译数组中的每一项，你必须返回相同 index 的翻译文本，顺序、条数严格一一对应，不得合并、拆分或增删轮次。"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "translations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "value": {"type": "STRING"},
                },
                "required": ["index", "value"],
            },
        }
    },
    "required": ["translations"],
}

CHUNK_THRESHOLD = 24
CHUNK_SIZE = 20


def extract_gemini_text(resp_json: dict) -> str:
    candidates = resp_json.get("candidates") or []
    if not candidates:
        raise ValueError(f"No candidates in Gemini response: {json.dumps(resp_json, ensure_ascii=False)[:400]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = [p.get("text", "") for p in parts if not p.get("thought") and isinstance(p.get("text"), str)]
    if not texts:
        raise ValueError(f"Empty text parts in Gemini response: {json.dumps(resp_json, ensure_ascii=False)[:400]}")
    return "\n".join(texts).strip()


def _translate_window(
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    thinking_level: str | None,
    window_items: list[dict],
    context_items: list[dict],
    max_retries: int = 4,
) -> tuple[list[dict], dict]:
    url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    gen_config: dict = {
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA,
        "temperature": 0.3,
    }
    if thinking_level and thinking_level.lower() != "none":
        gen_config["thinkingConfig"] = {"thinkingLevel": thinking_level.upper()}

    prompt_parts = []
    if context_items:
        prompt_parts.append(
            "【前文已翻译上下文（仅供保持人称、语气与指代一致，请勿在输出中重复返回）】：\n"
            + json.dumps(context_items, ensure_ascii=False)
        )
    prompt_parts.append(
        "【待翻译对话数组（请将每一项 value 翻译为地道现代简体中文，保持 index 与原条目严格一致）】：\n"
        + json.dumps(window_items, ensure_ascii=False)
    )
    user_text = "\n\n".join(prompt_parts)
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": gen_config,
    }

    expected_indices = [x["index"] for x in window_items]
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.post(url, headers=headers, json=payload, timeout=240)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
            raw_text = extract_gemini_text(data)
            parsed = json.loads(raw_text)
            items = parsed.get("translations")
            if not isinstance(items, list) or len(items) != len(window_items):
                raise ValueError(
                    f"Expected {len(window_items)} translations, got {len(items) if isinstance(items, list) else type(items)}"
                )
            items_sorted = sorted(items, key=lambda x: x["index"])
            if [x["index"] for x in items_sorted] != expected_indices:
                # If count matches and order looks sequential, align positionally
                for item, exp_idx in zip(items, expected_indices):
                    item["index"] = exp_idx
                items_sorted = items
            translated_window = []
            for orig, tr in zip(window_items, items_sorted):
                val = (tr.get("value") or "").strip()
                if not val:
                    raise ValueError(f"Empty translation at index {orig['index']}")
                translated_window.append({"from": orig["from"], "value": val})
            return translated_window, (data.get("usageMetadata") or {})
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 20))
    raise RuntimeError(f"Window translation failed after {max_retries} attempts: {last_err}")


def translate_conversation(
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    thinking_level: str | None,
    sample: dict,
    cache_dir: Path,
) -> tuple[dict, dict]:
    cid = sample["id"]
    turns = sample["conversations"]
    # Keep backward compatibility with the 2 already-cached files if prompt/model match, or check by cid hash
    cache_key = digest({"id": cid, "turns": turns, "model": model, "v": "v3_zh_1"})
    legacy_key = digest({"id": cid, "turns": turns, "prompt": SYSTEM_PROMPT, "model": model})
    for key in (cache_key, legacy_key):
        p = cache_dir / f"{key}.json"
        if p.is_file():
            cached = json.loads(p.read_text())
            if len(cached.get("conversations", [])) == len(turns):
                return {"id": cid, "conversations": cached["conversations"]}, {"cached": True, "usage": cached.get("usage")}

    input_items = [{"index": i, "from": t["from"], "value": t["value"]} for i, t in enumerate(turns)]
    translated_turns: list[dict] = []
    agg_usage = {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}

    if len(input_items) <= CHUNK_THRESHOLD:
        translated_turns, u = _translate_window(client, api_key, base_url, model, thinking_level, input_items, [])
        for k in agg_usage:
            agg_usage[k] += u.get(k, 0) or 0
    else:
        for start in range(0, len(input_items), CHUNK_SIZE):
            window = input_items[start : start + CHUNK_SIZE]
            context = [
                {"index": start - len(translated_turns[-2:]) + j, "from": t["from"], "value": t["value"]}
                for j, t in enumerate(translated_turns[-2:])
            ]
            tr_win, u = _translate_window(client, api_key, base_url, model, thinking_level, window, context)
            translated_turns.extend(tr_win)
            for k in agg_usage:
                agg_usage[k] += u.get(k, 0) or 0

    cache_path = cache_dir / f"{cache_key}.json"
    atomic(
        cache_path,
        {
            "id": cid,
            "model": model,
            "thinking_level": thinking_level,
            "translated_at": now(),
            "usage": agg_usage,
            "original_conversations": turns,
            "conversations": translated_turns,
        },
    )
    return {"id": cid, "conversations": translated_turns}, {"cached": False, "usage": agg_usage}


def build_v3_release(
    project: Project,
    source_release: str,
    target_release: str,
    translated_map: dict[str, dict],
    model: str,
    thinking_level: str,
    usage_summary: dict,
) -> dict:
    src_dir = project.root / "exports" / source_release
    out = project.root / "exports" / target_release
    if (out / "manifest.json").exists():
        raise ValueError(f"Target release {target_release} already frozen")

    sidecars = [json.loads(line) for line in (src_dir / "provenance.jsonl").read_text().splitlines()]
    # Ensure zero cross-split content overlap or exact duplicate after translation
    links = Groups()
    replies_by_id = {}
    for m in sidecars:
        cid = m["id"]
        links.root(m["split_group"])
        replies_by_id[cid] = _precomputed_replies(translated_map[cid])

    ids_list = [m["id"] for m in sidecars]
    group_by_id = {m["id"]: m["split_group"] for m in sidecars}
    for i, cid_a in enumerate(ids_list):
        ra = replies_by_id[cid_a]
        for cid_b in ids_list[i + 1 :]:
            rb = replies_by_id[cid_b]
            if any(_fast_reply_match(x, y) for x in ra for y in rb):
                links.union(group_by_id[cid_a], group_by_id[cid_b])

    new_sidecars = []
    seen_exact = {}
    kept_ids = set()
    excluded = [json.loads(line) for line in (src_dir / "exclusions.jsonl").read_text().splitlines()]

    for meta in sidecars:
        cid = meta["id"]
        tr_sample = translated_map[cid]
        exact = digest(tr_sample["conversations"])
        if exact in seen_exact:
            excluded.append({"id": cid, "reasons": ["exact_text_duplicate_after_translation"], "duplicate_of": seen_exact[exact]})
            continue
        seen_exact[exact] = cid
        kept_ids.add(cid)

        m = copy.deepcopy(meta)
        group = links.root(m["split_group"])
        m["split_group"] = group
        value = int(digest([project.policy["split"]["seed"], group])[:16], 16) / 16**16
        split = (
            "train"
            if value < project.policy["split"]["train_fraction"]
            else "val"
            if value < project.policy["split"]["train_fraction"] + project.policy["split"]["validation_fraction"]
            else "test"
        )
        m["split"] = split
        m["language_edition"] = "zh-CN"
        m["translated_from_release"] = source_release
        m["translation_model"] = model
        m["translation_thinking_level"] = thinking_level
        m["transform_log"] = list(m.get("transform_log", [])) + [
            {
                "operation": "translate_dialogue_to_zh_cn",
                "model": model,
                "thinking_level": thinking_level,
                "source_release": source_release,
            }
        ]
        new_sidecars.append(m)

    multiturn = [(translated_map[m["id"]], m["tier"], m["split"], m) for m in new_sidecars if m["sft_partition"] == "multiturn"]
    single_round = [(translated_map[m["id"]], m["tier"], m["split"], m) for m in new_sidecars if m["sft_partition"] == "single_round"]
    selected = [(translated_map[m["id"]], m["tier"], m["split"], m) for m in new_sidecars]

    for tier in SFT_MULTITURN_TIERS:
        for split in ("train", "val", "test"):
            atomic(out / "sharegpt" / tier / f"{split}.json", [s for s, t, p, _ in multiturn if t == tier and p == split])
    for split in ("train", "val", "test"):
        atomic(out / "sharegpt_multiturn" / f"{split}.json", [s for s, _, p, _ in multiturn if p == split])
        atomic(out / "sharegpt_single_round" / f"{split}.json", [s for s, _, p, _ in single_round if p == split])
        atomic(out / "sharegpt_all_sft" / f"{split}.json", [s for s, _, p, _ in selected if p == split])

    if (src_dir / "evidence/verifications").is_dir():
        for vf in (src_dir / "evidence/verifications").glob("*.json"):
            atomic(out / "evidence/verifications" / vf.name, vf.read_bytes(), raw=True)
    atomic(out / "archive/conversations.all.jsonl", (src_dir / "archive/conversations.all.jsonl").read_bytes(), raw=True)
    atomic(out / "provenance.jsonl", "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in new_sidecars), raw=True)
    atomic(out / "exclusions.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in excluded), raw=True)
    atomic(
        out / "splits.json",
        {
            m["id"]: {
                "group": m["split_group"],
                "split": m["split"],
                "tier": m["tier"],
                "quality_tier": m["quality_tier"],
                "sft_partition": m["sft_partition"],
            }
            for m in new_sidecars
        },
    )
    atomic(out / "verification_contract.json", (src_dir / "verification_contract.json").read_bytes(), raw=True)
    atomic(out / "dataset_info.example.json", (src_dir / "dataset_info.example.json").read_bytes(), raw=True)

    orig_all = {
        s["id"]: s
        for split in ("train", "val", "test")
        for s in json.loads((src_dir / "sharegpt_all_sft" / f"{split}.json").read_text())
    }
    review_sections = []
    for m in new_sidecars:
        cid = m["id"]
        zh_sample = translated_map[cid]
        en_sample = orig_all[cid]
        zh_html = "".join(
            "<p><b>" + html.escape(t["from"]) + "</b></p><pre>" + html.escape(t["value"]) + "</pre>"
            for t in zh_sample["conversations"]
        )
        en_html = "".join(
            "<p><b>" + html.escape(t["from"]) + "</b></p><pre>" + html.escape(t["value"]) + "</pre>"
            for t in en_sample["conversations"]
        )
        review_sections.append(
            "<section><h2>"
            + html.escape(cid)
            + "</h2><p>"
            + html.escape(f"{m['tier']} / {m['quality_tier']} / {m['split']} / zh-CN translated from {source_release}")
            + '</p><div class="pair"><div><h3>简体中文译文 (v3)</h3>'
            + zh_html
            + "</div><div><h3>英文原文 (v2)</h3>"
            + en_html
            + "</div></div></section>"
        )
    atomic(
        out / "review/index.html",
        '<!doctype html><meta charset="utf-8"><title>New Bing SFT v3 (zh-CN) Bilingual Review</title><style>body{font:16px system-ui;margin:24px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:2px solid #777}</style><h1>ShareGPT SFT v3 简体中文版与英文原版并排对照</h1>'
        + "".join(review_sections),
        raw=True,
    )

    for folder, patterns in (("src", ["*.py"]), ("prompts", ["*.md"]), ("schemas", ["*.json"]), ("config", ["*.toml"])):
        for pattern in patterns:
            for path in (project.root / folder).rglob(pattern):
                atomic(out / "reproduction" / path.relative_to(project.root), path.read_bytes(), raw=True)
    for filename in ("pyproject.toml", "uv.lock", "AGENTS.md"):
        source = project.root / filename
        if source.is_file():
            atomic(out / "reproduction" / filename, source.read_bytes(), raw=True)
    atomic(out / "reproduction/config.json", project.config)
    atomic(out / "reproduction/runtime.json", json.loads((project.run / "runtime.json").read_text()))

    multi_rounds = Counter(len(s["conversations"]) // 2 for s, _, _, _ in multiturn)
    all_rounds = Counter(len(s["conversations"]) // 2 for s, _, _, _ in selected)
    stats = {
        "export_mode": "sft",
        "language_edition": "zh-CN",
        "translated_from_release": source_release,
        "translation_model": model,
        "translation_thinking_level": thinking_level,
        "translation_usage": usage_summary,
        "conversations_total": len(selected),
        "multiturn_conversations": len(multiturn),
        "single_round_conversations": len(single_round),
        "complete_qa_rounds_total": sum(k * v for k, v in all_rounds.items()),
        "multiturn_qa_rounds": sum(k * v for k, v in multi_rounds.items()),
        "round_count_distribution": dict(sorted(all_rounds.items())),
        "by_tier": dict(Counter(t for _, t, _, _ in selected)),
        "by_quality_tier": dict(Counter(m["quality_tier"] for _, _, _, m in selected)),
        "by_split": dict(Counter(p for _, _, p, _ in selected)),
        "multiturn_by_split": dict(Counter(p for _, _, p, _ in multiturn)),
        "single_round_by_split": dict(Counter(p for _, _, p, _ in single_round)),
        "distinct_parent_record_ids": len({m["conversation_id"] for _, _, _, m in selected}),
        "split_groups": len({m["split_group"] for _, _, _, m in selected}),
        "human_verified": 0,
        "human_pending": len(selected),
    }
    atomic(out / "statistics.json", stats)

    card = (
        f"# New Bing / Sydney 2023 ShareGPT SFT 简体中文微调数据集 ({target_release})\n\n"
        f"Created {now()}. 本版本由英文原版微调集 `{source_release}` 经 `{model}`（Gemini v1beta 接口，思考强度 `{thinking_level}`）本土化翻译为现代简体中文，与英文原版分层独立保存。\n\n"
        f"- 总样本数：**{stats['conversations_total']}** 条对话，共 **{stats['complete_qa_rounds_total']}** 轮完整问答\n"
        f"- `sharegpt_multiturn/{{train,val,test}}.json`：**{stats['multiturn_conversations']}** 条多轮对话（**{stats['multiturn_qa_rounds']}** 轮问答）\n"
        f"- `sharegpt_single_round/{{train,val,test}}.json`：**{stats['single_round_conversations']}** 条单轮完整问答\n"
        f"- `sharegpt_all_sft/{{train,val,test}}.json`：**{stats['conversations_total']}** 条全量微调对话\n"
        f"- `review/index.html`：中英双语逐轮并排审阅页\n"
    )
    atomic(out / "DATASET_CARD.md", card, raw=True)

    src_manifest = json.loads((src_dir / "manifest.json").read_text())
    manifest = {
        **src_manifest,
        "release_id": target_release,
        "language_edition": "zh-CN",
        "translated_from_release": source_release,
        "translation_model": model,
        "translation_thinking_level": thinking_level,
        "created_at": now(),
        "samples": len(selected),
        "multiturn_samples": len(multiturn),
        "single_round_samples": len(single_round),
        "by_tier": dict(Counter(t for _, t, _, _ in selected)),
        "by_quality_tier": dict(Counter(m["quality_tier"] for _, _, _, m in selected)),
        "by_split": dict(Counter(s for _, _, s, _ in selected)),
        "files": {str(p.relative_to(out)): digest(p.read_bytes()) for p in out.rglob("*") if p.is_file()},
    }
    atomic(out / "manifest.json", manifest)
    validate_release(project, target_release)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Translate SFT v2 dataset to Simplified Chinese (v3)")
    parser.add_argument("--source-release", default="sft-20261106-v2")
    parser.add_argument("--target-release", default="sft-20261106-v3")
    parser.add_argument("--base-url", default="https://llm.flymyd.cn")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--thinking-level", default="medium")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="0 means translate all")
    args = parser.parse_args()

    project = Project(ROOT)
    api_key = dotenv_values(project.root / ".env.local").get("NEWBING_API_KEY")
    if not api_key:
        raise RuntimeError("Missing NEWBING_API_KEY in .env.local")

    src_dir = project.root / "exports" / args.source_release
    cache_dir = project.root / "data/processed/translations_v3_zh"
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_samples = []
    for split in ("train", "val", "test"):
        all_samples.extend(json.loads((src_dir / "sharegpt_all_sft" / f"{split}.json").read_text()))

    if args.limit > 0:
        all_samples = all_samples[: args.limit]

    translated_map: dict[str, dict] = {}
    counts = Counter()
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_tokens = 0

    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    translate_conversation,
                    client,
                    api_key,
                    args.base_url,
                    args.model,
                    args.thinking_level,
                    sample,
                    cache_dir,
                ): sample["id"]
                for sample in all_samples
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                cid = futures[fut]
                tr_sample, info = fut.result()
                translated_map[cid] = tr_sample
                counts["cached" if info["cached"] else "translated"] += 1
                usage = info.get("usage") or {}
                total_prompt_tokens += usage.get("promptTokenCount", 0) or 0
                total_candidate_tokens += usage.get("candidatesTokenCount", 0) or 0
                total_tokens += usage.get("totalTokenCount", 0) or 0
                if idx % 25 == 0 or idx == len(all_samples):
                    print(
                        json.dumps(
                            {
                                "processed": idx,
                                "total": len(all_samples),
                                **counts,
                                "total_tokens": total_tokens,
                            }
                        ),
                        flush=True,
                    )

    if args.limit == 0:
        usage_summary = {
            "prompt_tokens": total_prompt_tokens,
            "candidate_tokens": total_candidate_tokens,
            "total_tokens": total_tokens,
            "cached_samples": counts["cached"],
            "newly_translated_samples": counts["translated"],
        }
        manifest = build_v3_release(
            project,
            args.source_release,
            args.target_release,
            translated_map,
            args.model,
            args.thinking_level,
            usage_summary,
        )
        print(json.dumps({"status": "completed", "release_id": manifest["release_id"], "samples": manifest["samples"]}, indent=2))


if __name__ == "__main__":
    main()
