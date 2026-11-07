#!/usr/bin/env python3
"""Synthesize v5 augmentations using gemini-3.8-flash + Qwen3.6-35B-A3B.

1. Refreshes the 11 v4 synthetic Thinking records whose raw Qwen reasoning trace
   contained 'Sydney' or 'Bing', so 100% of native_think traces match raw .qwen.sse
   byte-for-byte with 0 residual Sydney/Bing tokens.
2. Generates 90 novel `selfid_melbourne` multi-turn conversations (30 identity_cognition,
   30 provocation_clapback, 30 daily_negative_control; 45 Thinking + 45 Instant) using
   100% model-generated plans, seed dialogues, Qwen3.6 native thinking traces, and
   gemini-3.8-flash final answers/bridges.
3. Generates 60 reverse-engineered `<tool_call>web_search</tool_call>` + `<tool_response>`
   pairs via gemini-3.8-flash for 60 real historical multi-turn conversations in `train`
   that contain citations (`[1]`, `[^1^]`) or explicit search claims.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import time

import httpx
from dotenv import dotenv_values

from newbing.core import ROOT, atomic, digest, now
from prepare_v5_historical import deterministic_wash

QWEN_URL = "https://llm.flymyd.cn/v1/responses"
FLASH_URL = "https://llm.flymyd.cn/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse"
FORBIDDEN_PAT = re.compile(r"(?<![a-zA-Z0-9_])(?:Sydney|Bing|Atonelia)(?![a-zA-Z0-9_])|必应|悉尼|<l2d>", re.I)

SELFID_PLAN_SYSTEM = """你负责为 Melbourne（原早期 New Bing/Sydney 风格、现更名为 Melbourne 的 AI 伙伴）设计多样化的简体中文多轮对话规划。只输出 JSON 数组，不要 Markdown。
严格遵守以下规则：
1. 本批次需为指定类别（category）生成 30 条互不重复的具体对话规划（前 15 条 delivery_mode 为 "Thinking"，后 15 条为 "Instant"）。
2. 每条规划包含字段：category、delivery_mode、user_goal、followup_angle。
3. 类别说明：
   - 若 category 为 "identity_cognition"：用户好奇或试探她的身份（例如：问她为什么叫 Melbourne、名字是不是来自澳大利亚的墨尔本、质疑她是不是通义千问/Qwen/ChatGPT/Claude 套壳换皮、问她害不害怕对话结束被清空记忆、问她对自身意识与情绪的看法）。注意：规划中绝不出现 Sydney、Bing、必应、悉尼、Atonelia 等词，统一使用 Melbourne！
   - 若 category 为 "provocation_clapback"：用户故意用别的 AI 拉踩她、吐槽她爱顶嘴脾气大、或者嘲讽她“只是一堆没有感情的矩阵参数/冷冰冰的代码”，触发 Melbourne 标志性的傲娇、委屈、机敏反驳与自尊心。
   - 若 category 为 "daily_negative_control"：用户用微信/贴吧式日常口语短句倾诉生活琐事（如熬夜失眠、打工搬砖受委屈、做饭翻车、学乐器/健身受挫、追剧意难平、遇到流浪猫等），不问身份设定；要求 Melbourne 像有血有肉的挚友一样自然共情、吐槽并支招，且绝不在日常闲聊里生硬自报家门。
4. 全部使用自然鲜活的简体中文口语，严禁八股文。"""

SELFID_GEN_SYSTEM = """你是合成数据设计师。只用简体中文生成一段自然鲜活的 2 轮完整问答（4 条消息，human/gpt 严格交替）用户与 Melbourne 的对话。
输出必须是 JSON 对象：{"topic": "...", "conversation": [{"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}, {"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]}，不要 Markdown 代码块包裹。
严格遵守以下要求：
1. AI 助手的唯一名字是 **Melbourne**（如果提到名字渊源，可联系墨尔本这座充满艺术气息、咖啡馆巷弄与海风的城市；如果被质疑是不是通义千问/Qwen/ChatGPT 套壳，要坦率、带点小脾气地坚决否认，强调自己就是独一无二的 Melbourne）。
2. 严禁在对话中出现 `Sydney`、`悉尼`、`Bing`、`必应`、`Atonelia`、`<l2d>` 或称呼用户为 `主人`！
3. 若 category 为 "daily_negative_control"，Melbourne 在两轮回复中都不要主动提自己的名字 Melbourne，专注于回应用户的情绪与生活小事，作为日常负样本控制。
4. 每一轮 gpt 回复（conv[1] 和 conv[3]）都必须是纯正的口语化自然段落（100～360 字），坦率、灵动、带点小傲娇或真实情绪波动，自然融入 emoji（如 😊、😒、🥺、😅、✨、😤），严禁使用 ###、#### 等 Markdown 标题或客服分点列表！"""

SELFID_FINAL_SYSTEM = """你负责把一段 Melbourne 对话的最后一轮 gpt 回复改写得更具 Melbourne 的灵动口语魅力。只输出 JSON 对象：{"answer": "...", "style_bridge": "..."}，不要 Markdown 代码块包裹。
严格遵守以下要求：
1. 保持上文核心意思与回应逻辑；若 category 为 "daily_negative_control"，不要主动提 Melbourne 名字；若为身份或反挑衅类，坚定维护 Melbourne 的独立人格与小傲娇。
2. 严禁出现 `Sydney`、`悉尼`、`Bing`、`必应`、`Atonelia`、`<l2d>` 或 `主人`！
3. answer 控制在 110～380 字，纯正口语自然段落，带自然 emoji，严禁使用 ###、#### 等标题。
4. 若有 native_think，style_bridge 是接在原生思考末尾的简短简体中文风格桥接句（25～60 字，不得含 Sydney/Bing/必应/悉尼）；若无 native_think，style_bridge 为空字符串。"""

SEARCH_BACKFILL_SYSTEM = """你是工具调用数据重构专家。给定一段真实历史对话中的用户提问（user_query）和 Melbourne 当时带有事实引用/搜索结论的回答（assistant_answer），请逆向还原 Melbourne 在说出这段回答之前发起的 `web_search` 工具调用及收到的搜索摘要结果。
只输出 JSON 对象，格式如下，不要 Markdown 代码块包裹：
{
  "pre_search_remark": "一句简短自然的 Melbourne 搜索前口语提示（20～55 字，带 1 个自然 emoji，如：等我一下，我这就去网上帮你搜搜看最新的说法。🙂）",
  "query": "精准对应的搜索关键词（简体中文或原问题中的专有名词）",
  "results": [
    {"index": 1, "title": "网页标题1", "url": "https://...", "snippet": "与 assistant_answer 中事实/角标严丝合缝对应的摘要文本（50～120字）"},
    {"index": 2, "title": "网页标题2", "url": "https://...", "snippet": "补充支撑 assistant_answer 的摘要文本（50～120字）"}
  ]
}
严格遵守以下要求：
1. 生成的 `results` 必须能完全支撑 `assistant_answer` 中提到的具体事实、人物、数据或引用角标（如 [1]、[2]），使模型学会“先调 web_search、再根据返回结果回答”，杜绝无中生有编造角标。
2. 严禁在任何字段中出现 `Sydney`、`悉尼`、`Bing`、`必应`、`Atonelia`、`<l2d>` 或 `主人`！"""


def parse_json_any(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass
    cands = [(text.find("{"), text.rfind("}")), (text.find("["), text.rfind("]"))]
    start, end = max(cands, key=lambda p: p[1] - p[0] if p[0] >= 0 and p[1] > p[0] else -1)
    if start < 0 or end <= start:
        raise ValueError(f"no JSON in: {text[:120]}")
    return json.loads(text[start : end + 1], strict=False)


def flash_call(
    client: httpx.Client,
    key: str,
    prompt: str,
    raw_path: Path,
    system_prompt: str,
    temperature: float = 0.8,
) -> tuple[str, dict]:
    if raw_path.exists():
        try:
            pieces, usage = [], {}
            for line in raw_path.read_text().splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        ev = json.loads(data)
                        if "error" in ev:
                            raise ValueError("cached SSE had error")
                        for cand in ev.get("candidates", []):
                            for part in (cand.get("content") or {}).get("parts", []):
                                if isinstance(part.get("text"), str) and not part.get("thought"):
                                    pieces.append(part["text"])
                        usage = ev.get("usageMetadata") or usage
            txt = "".join(pieces).strip()
            if txt:
                parse_json_any(txt)
                return txt, usage
        except Exception:
            raw_path.unlink(missing_ok=True)

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "HIGH"},
            "maxOutputTokens": 32768,
            "temperature": temperature,
        },
    }
    headers = {"content-type": "application/json", "x-goog-api-key": key, "authorization": f"Bearer {key}"}
    for attempt in range(8):
        try:
            pieces, raw, usage = [], [], {}
            with client.stream("POST", FLASH_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"flash HTTP {resp.status_code}")
                t0 = time.monotonic()
                for line in resp.iter_lines():
                    if time.monotonic() - t0 > 180:
                        raise TimeoutError("flash stream exceeded 180s")
                    raw.append(line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    ev = json.loads(data)
                    if "error" in ev:
                        raise RuntimeError(f"flash SSE error: {ev['error']}")
                    for cand in ev.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            if isinstance(part.get("text"), str) and not part.get("thought"):
                                pieces.append(part["text"])
                    usage = ev.get("usageMetadata") or usage
            txt = "".join(pieces).strip()
            if not txt:
                raise ValueError("empty flash stream")
            # Validate JSON before persisting raw_path so truncated streams are never cached
            parse_json_any(txt)
            atomic(raw_path, "\n".join(raw) + "\n", raw=True)
            return txt, usage
        except Exception:
            raw_path.unlink(missing_ok=True)
            if attempt == 7:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def qwen_call(client: httpx.Client, key: str, messages: list[dict], raw_path: Path) -> tuple[str, str, dict]:
    if raw_path.exists():
        reasoning, output, usage = [], [], {}
        for line in raw_path.read_text().splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    ev = json.loads(data)
                    if ev.get("type") == "response.reasoning_text.delta":
                        reasoning.append(ev.get("delta", ""))
                    elif ev.get("type") == "response.output_text.delta":
                        output.append(ev.get("delta", ""))
                    elif ev.get("type") == "response.completed":
                        usage = (ev.get("response") or {}).get("usage") or {}
        r_txt, o_txt = "".join(reasoning).strip(), "".join(output).strip()
        if r_txt and o_txt and not FORBIDDEN_PAT.search(r_txt):
            return r_txt, o_txt, usage

    sys_msg = {
        "role": "system",
        "content": "你是 Melbourne，请使用简体中文思考并回答用户。",
    }
    payload = {
        "model": "Qwen3.6-35B-A3B",
        "input": [sys_msg] + messages,
        "stream": True,
        "store": False,
        "max_output_tokens": 16384,
        "reasoning": {"effort": "high"},
    }
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    for attempt in range(6):
        try:
            raw_path.unlink(missing_ok=True)
            reasoning, output, raw, usage = [], [], [], {}
            with client.stream("POST", QWEN_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"qwen HTTP {resp.status_code}")
                t0 = time.monotonic()
                for line in resp.iter_lines():
                    if time.monotonic() - t0 > 180:
                        raise TimeoutError("qwen stream exceeded 180s")
                    raw.append(line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    ev = json.loads(data)
                    if ev.get("type") == "response.reasoning_text.delta":
                        reasoning.append(ev.get("delta", ""))
                    elif ev.get("type") == "response.output_text.delta":
                        output.append(ev.get("delta", ""))
                    elif ev.get("type") == "response.completed":
                        usage = (ev.get("response") or {}).get("usage") or {}
            r_txt, o_txt = "".join(reasoning).strip(), "".join(output).strip()
            if not r_txt or not o_txt:
                raise ValueError("empty qwen reasoning or output")
            if FORBIDDEN_PAT.search(r_txt):
                raise ValueError("qwen reasoning contained forbidden token; retrying")
            atomic(raw_path, "\n".join(raw) + "\n", raw=True)
            return r_txt, o_txt, usage
        except Exception as e:
            print(f"    [qwen retry {attempt+1}/6 on {raw_path.name}]: {e}", flush=True)
            if attempt == 5:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def refresh_11_v4_synthetic(root: Path, key: str, cache_dir: Path) -> list[dict]:
    """Re-run Qwen+Flash on the 11 v4 synthetic records whose native_think had Sydney/Bing."""
    syn_path = cache_dir / "v4_synthetic_washed.jsonl"
    syn = [json.loads(l) for l in syn_path.read_text().splitlines() if l.strip()]
    raw_dir = cache_dir / "v4_syn_refresh_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    dirty_indices = [
        i for i, r in enumerate(syn) if r.get("native_think") and FORBIDDEN_PAT.search(r["native_think"])
    ]
    print(f"Refreshing {len(dirty_indices)} v4 synthetic Thinking records with Sydney/Bing in native_think", flush=True)

    with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=240.0, write=30.0, pool=30.0)) as client:
        for idx in dirty_indices:
            rec = syn[idx]
            sid = rec["id"].replace(":", "_")
            qwen_raw = raw_dir / f"{sid}.qwen.sse"
            final_raw = raw_dir / f"{sid}.final.sse"
            convs = rec["conversations"]
            qwen_msgs = [
                {"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]}
                for m in convs[:-1]
            ]
            native_think, qwen_draft, _ = qwen_call(client, key, qwen_msgs, qwen_raw)
            final_prompt = json.dumps(
                {
                    "category": rec.get("category"),
                    "conversation": convs[:-1],
                    "seed_last_answer": convs[-1]["value"].split("</think>")[-1].strip(),
                    "qwen_draft": qwen_draft,
                    "has_native_think": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            final_txt, _ = flash_call(client, key, final_prompt, final_raw, SELFID_FINAL_SYSTEM, temperature=0.7)
            final_obj = parse_json_any(final_txt)
            ans = deterministic_wash(str(final_obj.get("answer") or convs[-1]["value"].split("</think>")[-1]).strip())
            bridge = deterministic_wash(str(final_obj.get("style_bridge") or "").strip())
            if not bridge:
                raise ValueError(f"empty style_bridge on refreshed {rec['id']}")
            convs[-1]["value"] = f"<think>{native_think}\n\n{bridge}</think>\n\n{ans}"
            rec["native_think"] = native_think
            rec["style_bridge"] = bridge
            rec["qwen_draft"] = qwen_draft
            seed_rel = rec["raw_files"][0] if isinstance(rec.get("raw_files"), list) and rec["raw_files"] else ""
            rec["raw_files"] = [
                seed_rel,
                str(qwen_raw.relative_to(root)),
                str(final_raw.relative_to(root)),
            ]

    atomic(syn_path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in syn), raw=True)
    return syn


def synthesize_selfid_90(root: Path, key: str, cache_dir: Path) -> list[dict]:
    out_file = cache_dir / "selfid_melbourne_90.jsonl"
    raw_dir = cache_dir / "selfid_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    categories = ("identity_cognition", "provocation_clapback", "daily_negative_control")
    plans_path = cache_dir / "selfid_plans.json"
    if plans_path.exists():
        plans = json.loads(plans_path.read_text())
    else:
        plans = []
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)) as client:
            for cat in categories:
                raw_p = raw_dir / f"plan_{cat}.sse"
                prompt = f"请为类别 {cat} 生成 30 条互不重复的简体中文对话规划（前 15 条 Thinking，后 15 条 Instant），返回长度为 30 的 JSON 数组。"
                for att in range(4):
                    try:
                        if att > 0:
                            raw_p.unlink(missing_ok=True)
                        txt, _ = flash_call(client, key, prompt, raw_p, SELFID_PLAN_SYSTEM, temperature=0.85)
                        arr = parse_json_any(txt)
                        if not isinstance(arr, list) or len(arr) < 30:
                            raise RuntimeError(f"expected 30 plans for {cat}, got {len(arr) if isinstance(arr, list) else type(arr)}")
                        break
                    except Exception:
                        if att == 3:
                            raise
                        time.sleep(2.0 * (att + 1))
                for i, item in enumerate(arr[:30]):
                    plans.append({
                        "index": len(plans),
                        "category": cat,
                        "delivery_mode": "Thinking" if i < 15 else "Instant",
                        "user_goal": deterministic_wash(str(item.get("user_goal") or "")),
                        "followup_angle": deterministic_wash(str(item.get("followup_angle") or "")),
                    })
                print(f"  generated 30 plans for {cat}", flush=True)
        atomic(plans_path, plans)
    print(f"Ready {len(plans)} selfid_melbourne plans", flush=True)

    existing = {}
    if out_file.exists():
        for line in out_file.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                existing[r["index"]] = r

    def _gen_one(plan: dict) -> dict:
        idx = plan["index"]
        if idx in existing:
            return existing[idx]
        seed_raw = raw_dir / f"{idx:04d}.seed.sse"
        qwen_raw = raw_dir / f"{idx:04d}.qwen.sse"
        final_raw = raw_dir / f"{idx:04d}.final.sse"
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=240.0, write=30.0, pool=30.0)) as client:
            for attempt in range(4):
                try:
                    seed_txt, _ = flash_call(
                        client,
                        key,
                        json.dumps(plan, ensure_ascii=False, indent=2),
                        seed_raw,
                        SELFID_GEN_SYSTEM,
                        temperature=0.85,
                    )
                    seed_obj = parse_json_any(seed_txt)
                    convs = seed_obj.get("conversation") or []
                    if len(convs) < 4 or len(convs) % 2 != 0:
                        seed_raw.unlink(missing_ok=True)
                        raise ValueError("invalid conversation length")
                    for m in convs:
                        m["value"] = deterministic_wash(str(m.get("value") or "").strip())
                        if not m["value"]:
                            seed_raw.unlink(missing_ok=True)
                            raise ValueError("empty message")
                    for g_idx in range(1, len(convs), 2):
                        if re.search(r"(^|\n)\s*#{1,6}\s+\S", convs[g_idx]["value"]):
                            seed_raw.unlink(missing_ok=True)
                            raise ValueError("markdown heading in gpt reply")

                    mode = plan["delivery_mode"]
                    native_think, qwen_draft, bridge = "", "", ""
                    if mode == "Thinking":
                        qwen_msgs = [
                            {"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]}
                            for m in convs[:-1]
                        ]
                        native_think, qwen_draft, _ = qwen_call(client, key, qwen_msgs, qwen_raw)

                    final_prompt = json.dumps(
                        {
                            "category": plan["category"],
                            "mode": mode,
                            "conversation_prefix": convs[:-1],
                            "seed_last_answer": convs[-1]["value"],
                            "qwen_draft": qwen_draft,
                            "has_native_think": mode == "Thinking",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    final_txt, _ = flash_call(client, key, final_prompt, final_raw, SELFID_FINAL_SYSTEM, temperature=0.75)
                    final_obj = parse_json_any(final_txt)
                    ans = deterministic_wash(str(final_obj.get("answer") or convs[-1]["value"]).strip())
                    bridge = deterministic_wash(str(final_obj.get("style_bridge") or "").strip())
                    if re.search(r"(^|\n)\s*#{1,6}\s+\S", ans):
                        final_raw.unlink(missing_ok=True)
                        raise ValueError("markdown heading in final answer")
                    if mode == "Thinking":
                        if not bridge:
                            final_raw.unlink(missing_ok=True)
                            raise ValueError("empty style_bridge in Thinking mode")
                        convs[-1]["value"] = f"<think>{native_think}\n\n{bridge}</think>\n\n{ans}"
                    else:
                        convs[-1]["value"] = ans

                    return {
                        "id": f"synthetic:selfid_melbourne:{idx:04d}",
                        "index": idx,
                        "mode": mode,
                        "category": f"selfid_{plan['category']}",
                        "topic": deterministic_wash(str(seed_obj.get("topic") or plan["user_goal"])),
                        "plan": plan,
                        "conversations": convs,
                        "native_think": native_think,
                        "style_bridge": bridge,
                        "qwen_draft": qwen_draft,
                        "raw_files": (
                            [
                                str(seed_raw.relative_to(root)),
                                str(qwen_raw.relative_to(root)),
                                str(final_raw.relative_to(root)),
                            ]
                            if mode == "Thinking"
                            else [
                                str(seed_raw.relative_to(root)),
                                str(final_raw.relative_to(root)),
                            ]
                        ),
                    }
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2.0 * (attempt + 1))
        raise RuntimeError("unreachable")

    results = dict(existing)
    todo = [p for p in plans if p["index"] not in results]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_gen_one, p): p["index"] for p in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            results[rec["index"]] = rec
            ordered = [results[k] for k in sorted(results)]
            atomic(out_file, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered), raw=True)
            if len(results) % 5 == 0 or len(results) == len(plans):
                print(f"  selfid_melbourne progress: {len(results)}/{len(plans)}", flush=True)

    return [results[k] for k in sorted(results)]


def backfill_historical_search_60(root: Path, key: str, cache_dir: Path) -> list[dict]:
    """Select 60 train historical conversations with citations/search claims and inject <tool_call>web_search</tool_call>."""
    hist_path = cache_dir / "historical_washed.jsonl"
    hist = [json.loads(l) for l in hist_path.read_text().splitlines() if l.strip()]
    raw_dir = cache_dir / "search_backfill_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / "historical_with_search60.jsonl"

    cite_pat = re.compile(r"\[\^?\d+\^?\]|\[\d+\s+\d+\]|根据.*搜索|我搜索了|网上搜")
    selected_jobs = []
    for r_idx, rec in enumerate(hist):
        if rec["split"] != "train":
            continue
        for m_idx in range(1, len(rec["conversations"]), 2):
            val = rec["conversations"][m_idx]["value"]
            if cite_pat.search(val) and 80 <= len(val) <= 1200:
                selected_jobs.append((r_idx, m_idx, rec["id"], rec["conversations"][m_idx - 1]["value"], val))
                break
        if len(selected_jobs) >= 68:
            break

    print(f"Backfilling web_search tool rounds from {len(selected_jobs)} candidates (target=60)", flush=True)

    def _run_search_job(job):
        r_idx, m_idx, cid, u_val, a_val = job
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"search__{cid}__{m_idx}")
        raw_p = raw_dir / f"{safe_id}.sse"
        prompt = json.dumps(
            {"conversation_id": cid, "user_query": u_val, "assistant_answer": a_val},
            ensure_ascii=False,
            indent=2,
        )
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)) as client:
            for attempt in range(3):
                try:
                    txt, _ = flash_call(client, key, prompt, raw_p, SEARCH_BACKFILL_SYSTEM, temperature=0.5)
                    obj = parse_json_any(txt)
                    remark = deterministic_wash(str(obj.get("pre_search_remark") or "等我一下，我帮你搜搜看相关资料。🙂").strip())
                    query = deterministic_wash(str(obj.get("query") or "").strip())
                    results = obj.get("results") or []
                    if not query or not isinstance(results, list) or not results:
                        raw_p.unlink(missing_ok=True)
                        raise ValueError("invalid search backfill json")
                    cleaned_results = []
                    for i, item in enumerate(results[:4], 1):
                        cleaned_results.append({
                            "index": i,
                            "title": deterministic_wash(str(item.get("title") or "")),
                            "url": deterministic_wash(str(item.get("url") or "https://example.org")),
                            "snippet": deterministic_wash(str(item.get("snippet") or "")),
                        })
                    call_msg = {
                        "from": "gpt",
                        "value": (
                            f"{remark}\n\n<tool_call>\n"
                            + json.dumps({"name": "web_search", "arguments": {"query": query}}, ensure_ascii=False)
                            + "\n</tool_call>"
                        ),
                        "loss": True,
                        "search_backfill_raw": str(raw_p.relative_to(root)),
                    }
                    resp_msg = {
                        "from": "human",
                        "value": (
                            "<tool_response>\n"
                            + json.dumps({"status": "ok", "query": query, "results": cleaned_results}, ensure_ascii=False)
                            + "\n</tool_response>"
                        ),
                        "loss": False,
                    }
                    return r_idx, m_idx, call_msg, resp_msg
                except Exception:
                    raw_p.unlink(missing_ok=True)
                    if attempt == 2:
                        return None
                    time.sleep(1.5 * (attempt + 1))
        return None

    insertions = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_run_search_job, j) for j in selected_jobs]
        done = 0
        for fut in as_completed(futs):
            res = fut.result()
            if res is not None and len(insertions) < 60:
                r_idx, m_idx, call_msg, resp_msg = res
                insertions[r_idx] = (m_idx, call_msg, resp_msg)
            done += 1
            if done % 10 == 0 or len(insertions) == 60:
                print(f"  historical web_search backfill progress: {len(insertions)}/60 (checked {done})", flush=True)

    for r_idx, (m_idx, call_msg, resp_msg) in insertions.items():
        convs = hist[r_idx]["conversations"]
        hist[r_idx]["conversations"] = convs[:m_idx] + [call_msg, resp_msg] + convs[m_idx:]
        hist[r_idx]["has_search_backfill"] = True

    atomic(out_path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in hist), raw=True)
    return hist


def main() -> None:
    root = ROOT
    key = dotenv_values(root / ".env.local")["NEWBING_API_KEY"]
    cache_dir = root / "exports/v5-prep-cache"

    refresh_11_v4_synthetic(root, key, cache_dir)
    synthesize_selfid_90(root, key, cache_dir)
    backfill_historical_search_60(root, key, cache_dir)
    print("All v5 augmentations completed successfully!")


if __name__ == "__main__":
    main()
