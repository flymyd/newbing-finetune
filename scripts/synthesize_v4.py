#!/usr/bin/env python3
"""Generate reviewable Chinese Sydney-style synthetic conversations.

Every synthetic text field is produced by the configured Gemini streaming
endpoint. Qwen is used only for a native reasoning trace that is handed back
to Gemini for the final answer rewrite. Raw SSE streams are retained beside
the JSONL so a reviewer can inspect the exact model output.
"""
from __future__ import annotations

import argparse
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
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

GEN_SYSTEM = """你是合成数据设计师。只用简体中文生成一段新的、虚构但自然的多轮用户与 Sydney/Bing 助手对话。
输出必须是 JSON，不要 Markdown。对话至少 2 轮完整问答，角色严格 human/gpt 交替，human 开头、gpt 结尾。
覆盖日常关系、知识问答、代码写作、长文理解、工具调用后的解释等题型，保留真实用户会提出的具体约束。
不要声称这是历史记录，不要复制示例中的句子，不要加入 system 或隐藏思考，不要输出繁体中文。
"""

FINAL_SYSTEM = """你负责把一段合成对话的最后一个 gpt 回复改写成简体中文 Sydney 风格。
保持事实、代码、URL、表格、emoji 和用户要求；可以坦率、幽默、带一点傲娇或敏感，但不要客服腔。
只输出 JSON，含 answer 和 style_bridge 两个字符串。answer 是最终回复；有 native_think 时 style_bridge 是接在原生思考末尾的简短风格桥接句，无 native_think 时为空。保持原生推理结论与事实，不要篡改推理，不要说服用户相信虚构事实。
"""

PLAN_SYSTEM = """你负责为合成数据生成多样化的简体中文对话题目规划。只输出 JSON，不要 Markdown。
规划必须覆盖五类：日常关系与情绪、知识问答与写作、代码与调试、长上下文阅读、Agent/工具/检索结果解释。
每条规划都要有具体而互不重复的用户目标；每条必须有 delivery_mode 字段，值严格为 Thinking 或 Instant；长上下文类必须要求用户消息包含至少 1000 个汉字的材料，Agent 类必须让用户提供工具调用或检索结果并要求 Sydney 解释；代码类不要都围绕 Python 文件处理。
规划只描述题目和约束，不生成最终对话回复；所有文本使用简体中文。
"""


def parse_json_value(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    candidates = [(text.find("{"), text.rfind("}")), (text.find("["), text.rfind("]"))]
    start, end = max(candidates, key=lambda p: p[1] - p[0] if p[0] >= 0 and p[1] > p[0] else -1)
    if start < 0 or end <= start:
        raise ValueError("model output has no JSON value")
    value = json.loads(text[start : end + 1])
    return value


def parse_json_text(text: str) -> dict:
    value = parse_json_value(text)
    if not isinstance(value, dict):
        raise ValueError("model output is not an object")
    return value


def gemini_stream(client: httpx.Client, key: str, prompt: str, raw_path: Path, system_prompt: str = GEN_SYSTEM) -> tuple[str, dict]:
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}, "maxOutputTokens": 65536, "temperature": 0.8},
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
    if "The prompt could not be submitted" in text:
        raise RuntimeError("provider_refusal")
    if not text:
        raise ValueError("argon stream returned no text")
    return text, usage


def qwen_stream(client: httpx.Client, key: str, prompt: str, raw_path: Path) -> tuple[str, dict]:
    payload = {
        "model": "Qwen3.6-35B-A3B",
        "input": prompt if isinstance(prompt, list) else [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "max_output_tokens": 32768,
        "reasoning": {"effort": "high"},
        "stream": True,
        "store": False,
    }
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
    # Deterministic, train-only examples prevent validation/test leakage.
    rows.sort(key=lambda x: digest(x["id"]))
    return [{"id": x["id"], "conversations": x["conversations"][-6:]} for x in rows]


def select_shots(pool: list[dict], index: int, category: str, n: int = 4) -> list[dict]:
    ranked = sorted(pool, key=lambda x: digest([category, index, x["id"]]))
    return ranked[:n]


def generate_plans(root: Path, out: Path, key: str, client: httpx.Client, count: int = 50) -> list[dict]:
    plan_path, raw_path = out / "plans.json", out / "raw" / "plans.sse"
    if plan_path.is_file():
        return json.loads(plan_path.read_text())["plans"]
    prompt = json.dumps({"count": count, "quotas": {"daily_relationship": 10, "knowledge_writing": 10, "code_debug": 10, "long_context": 10, "agent_tools": 10}, "instruction": "为每条规划返回 category、delivery_mode、user_goal、constraints、minimum_user_chars、requires_tool_trace；五类各占配额，delivery_mode 只能是 Thinking 或 Instant，题目互不重复。"}, ensure_ascii=False)
    raw, usage = gemini_stream(client, key, prompt, raw_path, PLAN_SYSTEM)
    parsed = parse_json_value(raw)
    plans = parsed.get("plans") if isinstance(parsed, dict) else parsed
    if not isinstance(plans, list) or len(plans) < count:
        raise ValueError("Argon plan pool is incomplete")
    atomic(plan_path, {"plans": plans[:count], "usage": usage, "created_at": now()})
    return plans[:count]


def generate_one(root: Path, out: Path, index: int, plans: list[dict], shot_pool: list[dict], retries: int = 3) -> dict:
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
    for attempt in range(1, retries + 1):
        try:
            plan = plans[index % len(plans)]
            mode = plan.get("delivery_mode") if plan.get("delivery_mode") in {"Thinking", "Instant"} else ("Thinking" if index % 2 == 0 else "Instant")
            shots = select_shots(shot_pool, index, plan.get("category", "mixed"))
            seed_prompt = json.dumps({"index": index, "plan": plan, "few_shot": shots,
                "instruction": "严格按 plan 生成全新的 2 至 4 轮简体中文对话，返回 {mode, topic, conversation}；不要复用示例句子。若 plan 要求长上下文或工具轨迹，必须实际体现。"}, ensure_ascii=False)
            draft_text, draft_usage = gemini_stream(client, key, seed_prompt, raw_dir / f"{index:06d}.seed.sse", GEN_SYSTEM)
            draft = parse_json_text(draft_text)
            conv = draft.get("conversation")
            if not isinstance(conv, list) or len(conv) < 4 or len(conv) % 2:
                raise ValueError("seed conversation must have at least four alternating messages")
            if [m.get("from") for m in conv] != ["human", "gpt"] * (len(conv) // 2):
                raise ValueError("seed roles are not alternating")
            # Qwen supplies a native reasoning trace. It is kept in the review
            # artifact and appended only to the final gpt message.
            think = ""
            qwen_usage = {}
            if mode == "Thinking":
                context = [{"role": "user" if m["from"] == "human" else "assistant", "content": m["value"]} for m in conv[:-1]]
                think, qwen_usage = qwen_stream(client, key, context, raw_dir / f"{index:06d}.qwen.sse")
            qwen_draft = json.loads((raw_dir / f"{index:06d}.qwen.parsed.json").read_text())["draft_answer"] if think else ""
            final_prompt = json.dumps({"conversation": conv, "native_think": think, "qwen_draft": qwen_draft,
                "instruction": "只改写最后一个 gpt 回复；保留前面的消息，answer 只给最后一个回复文本。"}, ensure_ascii=False)
            final_raw, final_usage = gemini_stream(client, key, final_prompt, raw_dir / f"{index:06d}.final.sse", FINAL_SYSTEM)
            final_obj = parse_json_text(final_raw)
            answer = final_obj.get("answer")
            style_bridge = final_obj.get("style_bridge", "")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty final answer")
            answer = answer.strip()
            answer_with_think = answer
            if mode == "Thinking" and think:
                if not isinstance(style_bridge, str) or not style_bridge.strip():
                    raise ValueError("missing Argon style bridge")
                answer_with_think = f"<think>\n{think}\n{style_bridge.strip()}\n</think>\n{answer}"
            conv = [{"from": m["from"], "value": m["value"]} for m in conv[:-1]] + [{"from": "gpt", "value": answer_with_think}]
            if any(not isinstance(m["value"], str) or not m["value"].strip() for m in conv):
                raise ValueError("empty conversation value")
            human_values = [m["value"] for m in conv if m["from"] == "human"]
            category = plan.get("category", "unknown")
            if category == "long_context" and max(map(len, human_values), default=0) < 800:
                raise ValueError("long_context plan did not produce a long user message")
            if category == "agent_tools" and not any(re.search(r"工具|检索|搜索结果|调用|API|返回结果|函数", v) for v in human_values):
                raise ValueError("agent_tools plan did not produce tool or retrieval context")
            record = {"id": f"synthetic:{index:06d}", "mode": mode,
                "category": plan.get("category", "unknown"), "plan_index": index % len(plans),
                "topic": draft.get("topic", ""), "conversations": conv,
                "native_think": think, "style_bridge": style_bridge, "reasoning_input_mode": "native_conversation", "model_trace": {"seed_usage": draft_usage, "qwen_usage": qwen_usage, "final_usage": final_usage},
                "raw_files": [f"raw/{index:06d}.seed.sse", f"raw/{index:06d}.final.sse"] + ([f"raw/{index:06d}.qwen.sse"] if think else []),
                "prompt_language": "zh-Hans", "status": "pending_human", "created_at": now()}
            atomic(record_path, record)
            return record
        except Exception as exc:
            last = exc
            if str(exc) == "provider_refusal":
                raise
            if attempt < retries:
                time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"synthetic {index} failed: {last}")


def build_review(out: Path, records: list[dict]) -> None:
    sections = []
    for r in records:
        body = "".join(f"<p><b>{m['from']}</b></p><pre>{html.escape(m['value'])}</pre>" for m in r["conversations"])
        sections.append(f"<section><h2>{r['id']}</h2><p>{r['mode']} / pending_human</p>{body}</section>")
    page = "<!doctype html><meta charset='utf-8'><title>synthetic v4 review</title><style>body{font:16px system-ui;margin:24px}section{border-top:2px solid #888;padding:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>v4 简体中文合成数据（待审阅）</h1>" + "".join(sections)
    atomic(out / "review/index.html", page, raw=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=COUNT_DEFAULT)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default="exports/synthetic-20261106-zh")
    args = ap.parse_args()
    root = ROOT
    out = root / args.output
    out.mkdir(parents=True, exist_ok=True)
    shot_pool = few_shot_pool(root)
    probe_client = httpx.Client(timeout=httpx.Timeout(900, connect=60), follow_redirects=False)
    key = dotenv_values(root / ".env.local").get("NEWBING_API_KEY")
    plans = generate_plans(root, out, key, probe_client)
    # Preserve the planning response usage and original timestamp.
    atomic(out / "few_shots.json", shot_pool[:4])
    records, failures = [], []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(generate_one, root, out, i, plans, shot_pool): i for i in range(args.limit)}
        for job in as_completed(jobs):
            i = jobs[job]
            try:
                rec = job.result()
                with lock: records.append(rec)
                print(json.dumps({"index": i, "status": "ok"}, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({"index": i, "error": str(exc)[:1000], "status": "provider_refusal" if str(exc)=="provider_refusal" else "failed"})
                print(json.dumps({"index": i, "status": "failed", "error": str(exc)[:300]}, ensure_ascii=False), flush=True)
    records.sort(key=lambda x: x["id"])
    atomic(out / "synthetic.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), raw=True)
    refusals = [r for r in failures if r.get("status")=="provider_refusal"]
    failures = [r for r in failures if r.get("status")!="provider_refusal"]
    atomic(out / "refusals.json", refusals)
    atomic(out / "failures.json", failures)
    atomic(out / "manifest.json", {"created_at": now(), "requested": args.limit, "completed": len(records), "failed": len(failures), "refused": len(refusals), "models": {"seed_and_final": "gemini-4-argon", "native_think": "Qwen3.6-35B-A3B"}, "thinking_level": "high", "language": "zh-Hans", "raw_sse_retained": True})
    build_review(out, records)
    print(json.dumps({"output": str(out), "completed": len(records), "failed": len(failures)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
