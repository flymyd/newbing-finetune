#!/usr/bin/env python3
"""Generate reviewable Chinese Sydney-style synthetic conversations.

Every synthetic text field is produced by the configured Gemini streaming
endpoint. Qwen is used only for a native reasoning trace that is handed back
to Gemini for the final answer rewrite. Raw SSE streams are retained beside
the JSONL so a reviewer can inspect the exact model output.
"""
from __future__ import annotations

import argparse
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
只输出 JSON：{\"answer\":\"...\"}。answer 必须是非空字符串，不能包含 JSON 之外的文字。
"""


def parse_json_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output has no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not an object")
    return value


def gemini_stream(client: httpx.Client, key: str, prompt: str, raw_path: Path) -> tuple[str, dict]:
    payload = {
        "systemInstruction": {"parts": [{"text": GEN_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}, "temperature": 0.8},
    }
    headers = {"content-type": "application/json", "x-goog-api-key": key, "authorization": f"Bearer {key}"}
    pieces, raw = [], []
    with client.stream("POST", ARGON_URL, headers=headers, json=payload) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"argon HTTP {resp.status_code}: {resp.text[:500]}")
        for line in resp.iter_lines():
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
    if not text:
        raise ValueError("argon stream returned no text")
    return text, usage


def qwen_stream(client: httpx.Client, key: str, prompt: str, raw_path: Path) -> tuple[str, dict]:
    payload = {
        "model": "Qwen3.6-35B-A3B",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "max_output_tokens": 32768,
        "reasoning": {"effort": "high"},
        "stream": True,
        "store": False,
    }
    headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
    pieces, raw, usage = [], [], {}
    with client.stream("POST", QWEN_URL, headers=headers, json=payload) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"qwen HTTP {resp.status_code}: {resp.text[:500]}")
        for line in resp.iter_lines():
            raw.append(line)
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            if event.get("type") == "response.output_text.delta":
                pieces.append(event.get("delta", ""))
            if event.get("type") == "response.completed":
                usage = (event.get("response") or {}).get("usage") or {}
    atomic(raw_path, "\n".join(raw) + "\n", raw=True)
    text = "".join(pieces).strip()
    if not text:
        raise ValueError("qwen stream returned no text")
    return text, usage


def few_shots(root: Path, n: int = 4) -> list[dict]:
    paths = sorted((root / "exports/sft-20261106-v3/sharegpt_multiturn").glob("train.json"))
    rows = []
    for path in paths:
        rows.extend(json.loads(path.read_text()))
    # Deterministic, train-only examples prevent validation/test leakage.
    rows.sort(key=lambda x: digest(x["id"]))
    return [{"id": x["id"], "conversations": x["conversations"][-6:]} for x in rows[:n]]


def extract_think(text: str) -> str:
    match = re.search(r"<think>(.*?)</think>", text, flags=re.S | re.I)
    return (match.group(1) if match else text).strip()


def generate_one(root: Path, out: Path, index: int, shots: list[dict], retries: int = 3) -> dict:
    key = dotenv_values(root / ".env.local").get("NEWBING_API_KEY")
    if not key:
        raise RuntimeError("NEWBING_API_KEY missing from .env.local")
    record_path = out / "records" / f"{index:06d}.json"
    if record_path.is_file():
        return json.loads(record_path.read_text())
    raw_dir = out / "raw"
    client = httpx.Client(timeout=httpx.Timeout(900, connect=60), follow_redirects=False)
    last = None
    for attempt in range(1, retries + 1):
        try:
            seed_prompt = json.dumps({"index": index, "few_shot": shots,
                "instruction": "先设计一段全新的 2 至 4 轮简体中文对话，返回 {mode, topic, conversation}；mode 在 Instant 与 Thinking 中选择。"}, ensure_ascii=False)
            draft_text, draft_usage = gemini_stream(client, key, seed_prompt, raw_dir / f"{index:06d}.seed.sse")
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
            if str(draft.get("mode", "")).lower() == "thinking":
                q_prompt = "请只输出 <think>...</think>。分析下面这段中文对话最后一个用户问题，给出可靠回答规划，并在 think 末尾写一句简短的风格桥接句：接下来用 Sydney 坦率、亲近、略带小傲娇的语气回复。\n" + json.dumps(conv, ensure_ascii=False)
                think_raw, qwen_usage = qwen_stream(client, key, q_prompt, raw_dir / f"{index:06d}.qwen.sse")
                think = extract_think(think_raw)
            final_prompt = json.dumps({"conversation": conv, "native_think": think,
                "instruction": "只改写最后一个 gpt 回复；保留前面的消息，answer 只给最后一个回复文本。"}, ensure_ascii=False)
            final_raw, final_usage = gemini_stream(client, key, FINAL_SYSTEM + "\n" + final_prompt, raw_dir / f"{index:06d}.final.sse")
            answer = parse_json_text(final_raw).get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty final answer")
            answer = answer.strip()
            answer_with_think = answer
            if str(draft.get("mode", "")).lower() == "thinking" and think:
                answer_with_think = f"<think>\n{think}\n</think>\n{answer}"
            conv = [{"from": m["from"], "value": m["value"]} for m in conv[:-1]] + [{"from": "gpt", "value": answer_with_think}]
            if any(not isinstance(m["value"], str) or not m["value"].strip() for m in conv):
                raise ValueError("empty conversation value")
            record = {"id": f"synthetic:{index:06d}", "mode": draft.get("mode", "Instant"),
                "topic": draft.get("topic", ""), "conversations": conv,
                "native_think": think, "model_trace": {"seed_usage": draft_usage, "qwen_usage": qwen_usage, "final_usage": final_usage},
                "raw_files": [f"raw/{index:06d}.seed.sse", f"raw/{index:06d}.final.sse"] + ([f"raw/{index:06d}.qwen.sse"] if think else []),
                "prompt_language": "zh-Hans", "status": "pending_human", "created_at": now()}
            atomic(record_path, record)
            return record
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"synthetic {index} failed: {last}")


def build_review(out: Path, records: list[dict]) -> None:
    sections = []
    for r in records:
        body = "".join(f"<p><b>{m['from']}</b></p><pre>{m['value']}</pre>" for m in r["conversations"])
        sections.append(f"<section><h2>{r['id']}</h2><p>{r['mode']} / pending_human</p>{body}</section>")
    html = "<!doctype html><meta charset='utf-8'><title>synthetic v4 review</title><style>body{font:16px system-ui;margin:24px}section{border-top:2px solid #888;padding:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>v4 简体中文合成数据（待审阅）</h1>" + "".join(sections)
    atomic(out / "review/index.html", html, raw=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=COUNT_DEFAULT)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default="exports/synthetic-20261106-zh")
    args = ap.parse_args()
    root = ROOT
    out = root / args.output
    out.mkdir(parents=True, exist_ok=True)
    shots = few_shots(root)
    atomic(out / "few_shots.json", shots)
    records, failures = [], []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(generate_one, root, out, i, shots): i for i in range(args.limit)}
        for job in as_completed(jobs):
            i = jobs[job]
            try:
                rec = job.result()
                with lock: records.append(rec)
                print(json.dumps({"index": i, "status": "ok"}, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({"index": i, "error": str(exc)[:1000]})
                print(json.dumps({"index": i, "status": "failed", "error": str(exc)[:300]}, ensure_ascii=False), flush=True)
    records.sort(key=lambda x: x["id"])
    atomic(out / "synthetic.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), raw=True)
    atomic(out / "failures.json", failures)
    atomic(out / "manifest.json", {"created_at": now(), "requested": args.limit, "completed": len(records), "failed": len(failures), "models": {"seed_and_final": "gemini-4-argon", "native_think": "Qwen3.6-35B-A3B"}, "thinking_level": "high", "language": "zh-Hans", "raw_sse_retained": True})
    build_review(out, records)
    print(json.dumps({"output": str(out), "completed": len(records), "failed": len(failures)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
