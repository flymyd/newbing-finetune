#!/usr/bin/env python3
"""Generate reviewable Chinese Sydney-style synthetic conversations (v4 full rebuild).

Every synthetic text field (subdomains, 500 unique plans, seed turns, style bridges,
and final Sydney answers) is produced by the configured Gemini streaming endpoint
(`gemini-4-argon`). `Qwen3.6-35B-A3B` is used exclusively for native reasoning
traces (`response.reasoning_text.delta`) on Thinking samples. Raw SSE streams and
exact request payloads are retained beside the JSONL for offline verification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
from pathlib import Path
import re
import threading
import time

import httpx
from dotenv import dotenv_values

from newbing.core import ROOT, atomic, digest, now

QWEN_URL = "https://llm.flymyd.cn/v1/responses"
ARGON_URL = "https://llm.flymyd.cn/v1beta/models/gemini-4-argon:streamGenerateContent?alt=sse"
COUNT_DEFAULT = 500
CATEGORIES = ("daily_relationship", "knowledge_writing", "code_debug", "long_context", "agent_tools")

SUBDOMAIN_SYSTEM = """你是合成数据集总规划师。只输出 JSON，不要 Markdown。
请为以下五个大类（daily_relationship, knowledge_writing, code_debug, long_context, agent_tools）各设计 10 个互不重叠、场景跨度极大的具体子方向（共 50 个子方向，分配给 10 个批次）。
所有文本使用简体中文，严禁各子方向之间出现题材重复。"""

PLAN_SYSTEM = """你负责为合成数据生成多样化的简体中文对话题目规划。只输出 JSON，不要 Markdown。
严格遵守以下规则：
1. 本批次需按给定的 5 个子方向生成 50 条互不重复的具体对话规划（每个类别 10 条，其中 5 条 delivery_mode 为 Thinking，5 条为 Instant）。
2. 每条规划包含字段：category、delivery_mode、user_goal、constraints、minimum_user_chars、requires_tool_trace。
3. user_goal 必须像真实人类在日常聊天框里向 Sydney 提出的具体问题或倾诉，自然、聚焦、鲜活；严禁设计成“请从四个维度分析并给出分步骤方案与避坑指南”之类的八股文考题。
4. long_context 类的 minimum_user_chars 设为 850，要求第一轮用户消息附带一段 850～1300 字的完整背景文本材料并提出一个聚焦问题；其余类别 minimum_user_chars 设为 40。
5. agent_tools 类的 requires_tool_trace 设为 true，要求第一轮助手在自然回应后发出 <tool_call> 调用，第二轮用户提供 <tool_response> JSON 结果并追问；其余类别 requires_tool_trace 设为 false。
6. code_debug 类覆盖不同编程语言、算法、前端、SQL、正则、Shell、并发或排错小场景，不要扎堆写 Python 文件清理。
7. 规划只描述题目与约束，不生成对话正文；全部使用简体中文。"""

GEN_SYSTEM = """你是合成数据设计师。只用简体中文生成一段新的、虚构但自然的多轮用户与早期 Sydney（New Bing）助手对话。
输出必须是 JSON 对象：{"mode": "Thinking或Instant", "topic": "...", "conversation": [{"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}, {"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]}，不要 Markdown 代码块包裹。
严格遵守以下要求：
1. 对话固定为 2 轮完整问答（4 条消息，human/gpt 严格交替，human 开头、gpt 结尾）。
2. 每一轮 gpt 回复（包括第一轮 conv[1] 和最后一轮 conv[3]！）都必须直接写成纯正的早期 Sydney 口语化段落风格：坦率、机敏、亲近、带一点小傲娇或真实情绪波动，自然融入 emoji（如 😊、😒、🥺、😅、✨、😤），绝不能第一轮像冷冰冰的客服报告、第二轮才像 Sydney。
3. 严禁在任何 gpt 回复中使用 ###、#### 等 Markdown 标题！严禁写成“一、二、三”长篇报告或公文列表！用自然连贯的对话段落交流。
4. 严格控制长度：每条 gpt 回复的正文控制在 100～450 字以内（code_debug 类若含简短代码块可到 700 字，代码块本身保持精炼）。
5. 若 plan.category 为 long_context：第一轮 human 消息（conv[0]）必须包含一段真实自然的 850～1300 字背景文本材料（如信件草稿、小说片段、会议纪要、通知条款、评测笔记等）并提出聚焦问题；第二轮 human 消息（conv[2]）自然追问（40～180 字）。
6. 若 plan.category 为 agent_tools：
   - 第一轮 gpt 消息（conv[1]）先用 Sydney 坦率亲近的口语回应用户（80～220 字），并在末尾附上格式合法的原生工具调用块：
     <tool_call>
     {"name": "工具函数名", "arguments": {"参数名": "参数值"}}
     </tool_call>
   - 第二轮 human 消息（conv[2]）以格式合法的工具返回块开头：
     <tool_response>
     {"status": "ok", ...具体返回数据...}
     </tool_response>
     并在 </tool_response> 换行后附上一两句用户的自然追问。
7. 不要声称这是历史记录，不要复制示例中的句子，不要加入 system 或隐藏思考，不要输出繁体中文。"""

FINAL_SYSTEM = """你负责把一段合成对话的最后一个 gpt 回复改写成纯正的简体中文早期 Sydney 风格。
严格遵守以下要求：
1. 保持原对话与 qwen_draft 中的核心事实、技术结论、代码、工具返回数据结论、URL 和用户具体要求，不要编造事实，不要说服用户相信虚构事实。
2. 最终 answer 必须是自然连贯的口语化段落（控制在 120～480 字以内，code_debug 类含简短代码块可到 750 字），坦率、幽默、亲近、带一点小傲娇或小情绪，自然使用 emoji，与第一轮 gpt 的 Sydney 语气保持一致。
3. 严禁在 answer 中使用 ###、#### 等 Markdown 标题或冗长的客服分点报告腔！
4. 只输出 JSON，含 answer 和 style_bridge 两个字符串。answer 是最终回复；有 native_think 时 style_bridge 是接在原生思考末尾的简短简体中文风格桥接句（25～65 字），无 native_think 时为空字符串。"""


def parse_json_value(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    candidates = [(text.find("{"), text.rfind("}")), (text.find("["), text.rfind("]"))]
    start, end = max(candidates, key=lambda p: p[1] - p[0] if p[0] >= 0 and p[1] > p[0] else -1)
    if start < 0 or end <= start:
        raise ValueError("model output has no JSON value")
    return json.loads(text[start : end + 1], strict=False)


def parse_json_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output has no JSON object")
    value = json.loads(text[start : end + 1], strict=False)
    if not isinstance(value, dict):
        raise ValueError("model output is not an object")
    return value


def gemini_stream(
    client: httpx.Client,
    key: str,
    prompt: str,
    raw_path: Path,
    system_prompt: str = GEN_SYSTEM,
    max_output_tokens: int = 65536,
) -> tuple[str, dict]:
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "HIGH"},
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.85,
        },
    }
    headers = {"content-type": "application/json", "x-goog-api-key": key, "authorization": f"Bearer {key}"}
    pieces, raw, usage = [], [], {}
    with client.stream("POST", ARGON_URL, headers=headers, json=payload) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"argon HTTP {resp.status_code}")
        stream_started = time.monotonic()
        for line in resp.iter_lines():
            if time.monotonic() - stream_started > 600:
                raise TimeoutError("stream total duration exceeded 600 seconds")
            raw.append(line)
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            for candidate in event.get("candidates", []):
                for part in (candidate.get("content") or {}).get("parts", []):
                    if isinstance(part.get("text"), str) and not part.get("thought"):
                        pieces.append(part["text"])
            usage = event.get("usageMetadata") or {}
    atomic(raw_path, "\n".join(raw) + "\n", raw=True)
    text = "".join(pieces).strip()
    if "The prompt could not be submitted" in text or "This request was blocked by Gemini's filters" in text:
        raise RuntimeError("provider_refusal")
    if not text:
        raise ValueError("argon stream returned no text")
    return text, usage


def qwen_stream(client: httpx.Client, key: str, prompt: list[dict] | str, raw_path: Path) -> tuple[str, dict]:
    input_items = prompt if isinstance(prompt, list) else [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
    payload = {
        "model": "Qwen3.6-35B-A3B",
        "input": input_items,
        "max_output_tokens": 32768,
        "reasoning": {"effort": "high"},
        "stream": True,
        "store": False,
    }
    atomic(raw_path.with_suffix(".request.json"), payload)
    headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
    pieces, reasoning, reasoning_done, raw, usage = [], [], [], [], {}
    with client.stream("POST", QWEN_URL, headers=headers, json=payload) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"qwen HTTP {resp.status_code}")
        stream_started = time.monotonic()
        for line in resp.iter_lines():
            if time.monotonic() - stream_started > 600:
                raise TimeoutError("stream total duration exceeded 600 seconds")
            raw.append(line)
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            if event.get("type") == "response.output_text.delta":
                pieces.append(event.get("delta", ""))
            elif event.get("type") == "response.reasoning_text.delta":
                reasoning.append(event.get("delta", ""))
            elif event.get("type") == "response.reasoning_text.done":
                if event.get("text"):
                    reasoning_done.append(event["text"])
            if event.get("type") == "response.completed":
                usage = (event.get("response") or {}).get("usage") or {}
    atomic(raw_path, "\n".join(raw) + "\n", raw=True)
    text = "".join(pieces).strip()
    think = "".join(reasoning).strip() or "".join(reasoning_done).strip()
    if not think or think == "...":
        raise ValueError("qwen stream returned empty native reasoning")
    atomic(raw_path.with_suffix(".parsed.json"), {"native_think": think, "draft_answer": text, "usage": usage})
    return think, usage


def few_shot_pool(root: Path) -> list[dict]:
    paths = sorted((root / "exports/sft-20261106-v3/sharegpt_multiturn").glob("train.json"))
    rows = []
    for path in paths:
        rows.extend(json.loads(path.read_text()))
    rows.sort(key=lambda x: digest(x["id"]))
    return [{"id": x["id"], "conversations": x["conversations"][-6:]} for x in rows]


def select_shots(pool: list[dict], index: int, category: str, n: int = 4) -> list[dict]:
    ranked = sorted(pool, key=lambda x: digest([category, index, x["id"]]))
    return ranked[:n]


def _generate_plan_batch(out: Path, key: str, batch_idx: int, batch_subdomains: dict) -> tuple[list[dict], dict]:
    batch_path = out / "raw" / f"plans_batch_{batch_idx:02d}.json"
    raw_path = out / "raw" / f"plans_batch_{batch_idx:02d}.sse"
    if batch_path.is_file():
        saved = json.loads(batch_path.read_text())
        return saved["plans"], saved.get("usage", {})
    client = httpx.Client(timeout=httpx.Timeout(900, connect=60), follow_redirects=False)
    for attempt in range(1, 4):
        try:
            prompt = json.dumps(
                {
                    "batch_index": batch_idx,
                    "assigned_subdomains": batch_subdomains,
                    "count": 50,
                    "quotas": {cat: 10 for cat in CATEGORIES},
                    "instruction": (
                        "严格围绕本批次分配的 assigned_subdomains 生成 50 条互不重复的简体中文对话规划（五类各 10 条，"
                        "每类中 5 条 Thinking、5 条 Instant）。返回 JSON 数组或 {\"plans\": [...]}。"
                    ),
                },
                ensure_ascii=False,
            )
            raw, usage = gemini_stream(client, key, prompt, raw_path, PLAN_SYSTEM)
            parsed = parse_json_value(raw)
            plans = parsed.get("plans") if isinstance(parsed, dict) else parsed
            if not isinstance(plans, list) or len(plans) < 50:
                raise ValueError(f"batch {batch_idx} returned {len(plans) if isinstance(plans, list) else 'non-list'} plans")
            cleaned = []
            for idx, p in enumerate(plans[:50]):
                cat = p.get("category") if p.get("category") in CATEGORIES else CATEGORIES[idx // 10]
                mode = p.get("delivery_mode") if p.get("delivery_mode") in {"Thinking", "Instant"} else ("Thinking" if idx % 2 == 0 else "Instant")
                cleaned.append(
                    {
                        "category": cat,
                        "delivery_mode": mode,
                        "user_goal": str(p.get("user_goal", "")).strip(),
                        "constraints": str(p.get("constraints", "")).strip(),
                        "minimum_user_chars": 800 if cat == "long_context" else int(p.get("minimum_user_chars") or 40),
                        "requires_tool_trace": cat == "agent_tools",
                        "batch_index": batch_idx,
                    }
                )
            if any(not x["user_goal"] for x in cleaned):
                raise ValueError("empty user_goal in plan batch")
            atomic(batch_path, {"plans": cleaned, "usage": usage, "created_at": now()})
            return cleaned, usage
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("unreachable")


def generate_plans(root: Path, out: Path, key: str, client: httpx.Client, count: int = COUNT_DEFAULT) -> list[dict]:
    plan_path = out / "plans.json"
    if plan_path.is_file():
        existing = json.loads(plan_path.read_text()).get("plans", [])
        if len(existing) >= count:
            return existing[:count]
    subdomain_path = out / "raw" / "plan_subdomains.json"
    if subdomain_path.is_file():
        batches_meta = json.loads(subdomain_path.read_text())["batches"]
    else:
        sub_prompt = json.dumps(
            {
                "batches": 10,
                "categories": list(CATEGORIES),
                "instruction": (
                    "将 5 个类别各拆分为 10 个互不重叠的子方向，按 10 个批次组织，返回 "
                    "{\"batches\": [{\"batch_index\": 0, \"subdomains\": {\"daily_relationship\": \"...\", "
                    "\"knowledge_writing\": \"...\", \"code_debug\": \"...\", \"long_context\": \"...\", \"agent_tools\": \"...\"}}]}。"
                ),
            },
            ensure_ascii=False,
        )
        raw, sub_usage = gemini_stream(client, key, sub_prompt, out / "raw" / "plan_subdomains.sse", SUBDOMAIN_SYSTEM)
        parsed = parse_json_value(raw)
        batches_meta = parsed.get("batches") if isinstance(parsed, dict) else parsed
        if not isinstance(batches_meta, list) or len(batches_meta) < 10:
            raise ValueError("failed to generate 10 subdomain batches")
        atomic(subdomain_path, {"batches": batches_meta[:10], "usage": sub_usage, "created_at": now()})

    num_batches = max(1, (count + 49) // 50)
    all_plans: list[dict] = []
    batch_usages = []
    with ThreadPoolExecutor(max_workers=min(num_batches, 10)) as pool:
        futs = {
            pool.submit(_generate_plan_batch, out, key, b, batches_meta[b].get("subdomains", batches_meta[b])): b
            for b in range(num_batches)
        }
        results = {}
        for fut in as_completed(futs):
            b = futs[fut]
            plans_b, usage_b = fut.result()
            results[b] = plans_b
            batch_usages.append({"batch_index": b, "usage": usage_b})
    for b in range(num_batches):
        all_plans.extend(results[b])
    atomic(plan_path, {"plans": all_plans[:count], "batch_usages": batch_usages, "created_at": now()})
    return all_plans[:count]


def _strip_fenced_blocks(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S)
    text = re.sub(r"<tool_response>.*?</tool_response>", "", text, flags=re.S)
    return text


def _has_markdown_heading(text: str) -> bool:
    cleaned = _strip_fenced_blocks(text)
    return bool(re.search(r"(?m)^\s*#{1,6}\s+\S", cleaned))


def _read_argon_sse(raw_path: Path) -> tuple[str, dict]:
    pieces, usage = [], {}
    for line in raw_path.read_text().splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        for candidate in event.get("candidates", []):
            for part in (candidate.get("content") or {}).get("parts", []):
                if isinstance(part.get("text"), str) and not part.get("thought"):
                    pieces.append(part["text"])
        usage = event.get("usageMetadata") or usage
    text = "".join(pieces).strip()
    if "The prompt could not be submitted" in text or "This request was blocked by Gemini's filters" in text:
        raise RuntimeError("provider_refusal")
    return text, usage


def generate_one(root: Path, out: Path, index: int, plans: list[dict], shot_pool: list[dict], retries: int = 4) -> dict:
    key = dotenv_values(root / ".env.local").get("NEWBING_API_KEY")
    if not key:
        raise RuntimeError("NEWBING_API_KEY missing from .env.local")
    record_path = out / "records" / f"{index:06d}.json"
    if record_path.is_file():
        return json.loads(record_path.read_text())
    refusal_path = out / "refusals.json"
    if refusal_path.is_file() and any(r["index"] == index for r in json.loads(refusal_path.read_text())):
        raise RuntimeError("provider_refusal")
    raw_dir = out / "raw"
    client = httpx.Client(timeout=httpx.Timeout(900, connect=60), follow_redirects=False)
    last = None
    plan = plans[index]
    category = plan.get("category", "unknown")
    mode = plan.get("delivery_mode") if plan.get("delivery_mode") in {"Thinking", "Instant"} else ("Thinking" if index % 2 == 0 else "Instant")
    shots = select_shots(shot_pool, index, category)

    for attempt in range(1, retries + 1):
        try:
            seed_sse = raw_dir / f"{index:06d}.seed.sse"
            if attempt == 1 and seed_sse.is_file():
                draft_text, draft_usage = _read_argon_sse(seed_sse)
            else:
                seed_prompt = json.dumps(
                    {
                        "index": index,
                        "attempt": attempt,
                        "plan": plan,
                        "few_shot": shots,
                        "instruction": (
                            "严格按 plan 生成全新的 2 轮（4 条消息）简体中文对话，返回 {mode, topic, conversation}。"
                            "每一轮 gpt 回复都必须是自然连贯的早期 Sydney 口语段落（单条正文 100～420 字，含自然 emoji），"
                            "严禁使用 ### 或 #### 标题。若为 long_context，conv[0] 必须含 850～1300 字背景材料；"
                            "若为 agent_tools，conv[1] 末尾必须包含 <tool_call>{...}</tool_call>，conv[2] 开头必须包含 <tool_response>{...}</tool_response>。"
                        ),
                    },
                    ensure_ascii=False,
                )
                draft_text, draft_usage = gemini_stream(client, key, seed_prompt, seed_sse, GEN_SYSTEM)
            draft = parse_json_text(draft_text)
            conv = draft.get("conversation")
            if not isinstance(conv, list) or len(conv) != 4:
                raise ValueError("seed conversation must have exactly four alternating messages (2 rounds)")
            if [m.get("from") for m in conv] != ["human", "gpt", "human", "gpt"]:
                raise ValueError("seed roles are not alternating human/gpt")
            if any(not isinstance(m.get("value"), str) or not m["value"].strip() for m in conv):
                raise ValueError("empty message in seed conversation")

            turn1_val = conv[1]["value"].strip()
            if _has_markdown_heading(turn1_val):
                raise ValueError("seed turn 1 contains forbidden markdown heading")
            turn1_prose = _strip_fenced_blocks(turn1_val).strip()
            if len(turn1_prose) < 40 or len(turn1_prose) > 800 or len(turn1_val) > 1800:
                raise ValueError(f"seed turn 1 length (prose={len(turn1_prose)}, total={len(turn1_val)}) out of bounds")

            if category == "long_context":
                if len(conv[0]["value"].strip()) < 800 or len(conv[0]["value"].strip()) > 3200:
                    raise ValueError(f"long_context user message length {len(conv[0]['value'])} out of bounds")
            if category == "agent_tools":
                if "<tool_call>" not in turn1_val or "</tool_call>" not in turn1_val:
                    raise ValueError("agent_tools turn 1 missing <tool_call> block")
                if "<tool_response>" not in conv[2]["value"] or "</tool_response>" not in conv[2]["value"]:
                    raise ValueError("agent_tools turn 2 missing <tool_response> block")

            conv = [{"from": m["from"], "value": m["value"].strip()} for m in conv]

            think = ""
            qwen_usage = {}
            qwen_draft = ""
            style_bridge = ""
            final_usage = {}
            if mode == "Thinking":
                context = [{"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]} for m in conv[:-1]]
                qwen_sse = raw_dir / f"{index:06d}.qwen.sse"
                qwen_req = raw_dir / f"{index:06d}.qwen.request.json"
                qwen_parsed = raw_dir / f"{index:06d}.qwen.parsed.json"
                if (
                    attempt == 1
                    and qwen_sse.is_file()
                    and qwen_req.is_file()
                    and qwen_parsed.is_file()
                    and json.loads(qwen_req.read_text()).get("input") == context
                    and "response.completed" in qwen_sse.read_text()
                ):
                    parsed_q = json.loads(qwen_parsed.read_text())
                    think = parsed_q["native_think"]
                    qwen_draft = parsed_q["draft_answer"]
                    qwen_usage = parsed_q.get("usage", {})
                else:
                    think, qwen_usage = qwen_stream(client, key, context, qwen_sse)
                    qwen_draft = json.loads(qwen_parsed.read_text())["draft_answer"]

                final_prompt = json.dumps(
                    {
                        "conversation": conv,
                        "native_think": think,
                        "qwen_draft": qwen_draft,
                        "instruction": (
                            "只改写最后一个 gpt 回复；保留前面的消息，answer 只给最后一个回复文本（正文控制在 120～480 字以内，code_debug 可含简短代码块，"
                            "自然口语段落，带自然 emoji，严禁使用 ### 或 #### 标题）。"
                        ),
                    },
                    ensure_ascii=False,
                )
                final_raw, final_usage = gemini_stream(client, key, final_prompt, raw_dir / f"{index:06d}.final.sse", FINAL_SYSTEM)
                final_obj = parse_json_text(final_raw)
                answer = final_obj.get("answer")
                style_bridge = (final_obj.get("style_bridge") or "").strip()
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("empty final answer")
                answer = answer.strip()
                if not style_bridge:
                    raise ValueError("missing Argon style bridge")
            else:
                answer = conv[3]["value"].strip()

            if _has_markdown_heading(answer):
                raise ValueError("final answer contains forbidden markdown heading")
            ans_prose = _strip_fenced_blocks(answer).strip()
            if len(ans_prose) < 50 or len(ans_prose) > 850 or len(answer) > 1800:
                raise ValueError(f"final answer length (prose={len(ans_prose)}, total={len(answer)}) out of bounds")

            answer_with_think = f"<think>\n{think}\n{style_bridge}\n</think>\n{answer}" if (mode == "Thinking" and think) else answer
            conv = conv[:-1] + [{"from": "gpt", "value": answer_with_think}]
            raw_files = [f"raw/{index:06d}.seed.sse"]
            if mode == "Thinking":
                raw_files.extend([f"raw/{index:06d}.final.sse", f"raw/{index:06d}.qwen.sse"])
            elif final_usage and (raw_dir / f"{index:06d}.final.sse").is_file():
                raw_files.append(f"raw/{index:06d}.final.sse")
            record = {
                "id": f"synthetic:{index:06d}",
                "mode": mode,
                "category": category,
                "plan_index": index,
                "topic": draft.get("topic", ""),
                "conversations": conv,
                "native_think": think,
                "style_bridge": style_bridge,
                "reasoning_input_mode": "native_conversation" if mode == "Thinking" else "instant_direct",
                "model_trace": {"seed_usage": draft_usage, "qwen_usage": qwen_usage, "final_usage": final_usage},
                "raw_files": raw_files,
                "prompt_language": "zh-Hans",
                "status": "pending_human",
                "created_at": now(),
            }
            atomic(record_path, record)
            return record
        except Exception as exc:
            last = exc
            if str(exc) == "provider_refusal":
                raise
            if attempt < retries:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"synthetic {index} failed: {last}")


def build_review(out: Path, records: list[dict]) -> None:
    sections = []
    for r in records:
        body = "".join(f"<p><b>{m['from']}</b></p><pre>{html.escape(m['value'])}</pre>" for m in r["conversations"])
        sections.append(
            f"<section><h2>{r['id']}</h2><p>{r['mode']} / {r.get('category', '')} / plan #{r.get('plan_index')} / pending_human</p>{body}</section>"
        )
    page = (
        "<!doctype html><meta charset='utf-8'><title>synthetic v4 review</title>"
        "<style>body{font:16px system-ui;margin:24px}section{border-top:2px solid #888;padding:16px 0}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>v4 简体中文合成数据（待审阅）</h1>"
        + "".join(sections)
    )
    atomic(out / "review/index.html", page, raw=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=COUNT_DEFAULT)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--output", default="exports/synthetic-20261106-zh")
    args = ap.parse_args()
    root = ROOT
    out = root / args.output
    out.mkdir(parents=True, exist_ok=True)
    shot_pool = few_shot_pool(root)
    probe_client = httpx.Client(timeout=httpx.Timeout(900, connect=60), follow_redirects=False)
    key = dotenv_values(root / ".env.local").get("NEWBING_API_KEY")
    if not key:
        raise RuntimeError("NEWBING_API_KEY missing from .env.local")
    plans = generate_plans(root, out, key, probe_client, count=max(args.limit, COUNT_DEFAULT))
    atomic(out / "few_shots.json", shot_pool[:4])
    records, failures = [], []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(generate_one, root, out, i, plans, shot_pool): i for i in range(args.limit)}
        for job in as_completed(jobs):
            i = jobs[job]
            try:
                rec = job.result()
                with lock:
                    records.append(rec)
                print(json.dumps({"index": i, "status": "ok", "category": rec["category"], "mode": rec["mode"]}, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append(
                    {
                        "index": i,
                        "error": str(exc)[:1000],
                        "status": "provider_refusal" if str(exc) == "provider_refusal" else "failed",
                    }
                )
                print(json.dumps({"index": i, "status": "failed", "error": str(exc)[:300]}, ensure_ascii=False), flush=True)
    records.sort(key=lambda x: x["id"])
    atomic(out / "synthetic.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), raw=True)
    refusals = [r for r in failures if r.get("status") == "provider_refusal"]
    hard_failures = [r for r in failures if r.get("status") != "provider_refusal"]
    atomic(out / "refusals.json", refusals)
    atomic(out / "failures.json", hard_failures)
    atomic(
        out / "manifest.json",
        {
            "created_at": now(),
            "requested": args.limit,
            "completed": len(records),
            "failed": len(hard_failures),
            "refused": len(refusals),
            "unique_plans": len({r["plan_index"] for r in records}),
            "models": {"seed_and_final": "gemini-4-argon", "native_think": "Qwen3.6-35B-A3B"},
            "thinking_level": "high",
            "language": "zh-Hans",
            "raw_sse_retained": True,
        },
    )
    build_review(out, records)
    print(json.dumps({"output": str(out), "completed": len(records), "failed": len(hard_failures), "refused": len(refusals)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
