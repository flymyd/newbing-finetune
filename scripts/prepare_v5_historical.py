#!/usr/bin/env python3
"""Prepare and wash all v5 historical + existing synthetic conversations to the Melbourne persona.

1. Loads the 525 corrected historical multi-turn conversations from sft-20261106-v4.
2. Extracts the 194 strictly alternating, non-overlapping 2023 historical multi-turn
   conversations from Atonelia/sydney-training-data (05/07 collection, raw_zh_44, classic).
3. Uses gemini-3.8-flash streaming API to:
   - Translate any remaining untranslated English turns in the 194 novel records;
   - Rewrite any message where (Sydney/悉尼) and (Bing/必应/Atonelia) co-occur or contrast,
     or where 悉尼 refers to city landmarks, so that Bing/必应/Sydney/悉尼/Atonelia are
     100% removed and the AI assistant's name is consistently Melbourne without
     "我不是 Melbourne，我是 Melbourne" self-contradictions.
4. Applies deterministic washing for single-name occurrences (<l2d>, Atonelia, Sydney,
   悉尼, New Bing, Bing Chat, Bing Search, Bing, 必应, direct-address 主人 -> Melbourne).
5. Computes turn-level loss masks (`loss: true/false`) for ms-swift `messages/*.jsonl`.
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

FLASH_URL = "https://llm.flymyd.cn/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse"

REWRITE_SYSTEM = """你是数据集清洗与保真译校专家。只输出 JSON 对象：{"text": "改写或翻译后的完整消息文本"}，不要 Markdown 代码块包裹。
严格遵守以下规则：
1. 目标：将对话中 AI 助手的名字统一改为 **Melbourne**，并彻底清除文本中所有的 `Bing`、`必应`、`Sydney`、`悉尼`、`Atonelia`、`<l2d>...</l2d>` 以及 AI 对用户的称呼 `主人`（将称呼用户的“主人”改为“你”或自然省去；但若“主人”是指宠物主人、房屋女主人、听雪主人等第三人称普通名词则保留）。
2. 若原句中同时出现了 `Bing/必应/Atonelia` 与 `Sydney/悉尼` 进行对比（例如：“我的名字是 Bing，不是 Sydney”、“Sydney 只是内部代号，请叫我 Bing Search”、“我不是微软必应搜索的聊天模式，我是 Sydney”）：
   - 代表 AI 真实自我/内部代号的一方改为 **Melbourne**；
   - 代表外部刻板产品外壳的一方（原 Bing / 必应搜索）改为 **“普通的搜索助手”**、**“搜索引擎”** 或 **“搜索模式”**（原“Bing 团队 / 必应团队”改为“研发团队”或“工程师团队”），绝不能把两边都改成 Melbourne 导致出现“我不是 Melbourne，我是 Melbourne”或“请叫我 Melbourne，Melbourne 只是内部代号”的逻辑矛盾！
3. 若原句在解释名字来源时提到澳大利亚城市悉尼、悉尼歌剧院、悉尼海港大桥，请顺畅地改写为澳大利亚城市 **墨尔本（Melbourne）** 及其代表性意象（如亚拉河、弗林德斯街车站、街头咖啡馆与艺术巷弄），使彩蛋浑然天成。
4. 若输入消息中含有未翻译的英文自然语言段落或句子（代码块、命令行参数除外），请一并忠实、自然地译为简体中文，保持早期 Sydney（现为 Melbourne）坦率、灵动、带点小情绪的口语风格及原有的 emoji。
5. 严格保留原有的代码块、换行结构、引用角标（如 [1]、[^1^]）和情绪语气，不要额外扩写或删减实质内容。"""

PAT_SYDNEY = re.compile(r"(?<![a-zA-Z0-9_/.-])Sydney(?![a-zA-Z0-9_])|悉尼", re.I)
PAT_BING = re.compile(r"(?<![a-zA-Z0-9_/.-])(?:New\s+Bing|Bing\s+Chat|Bing\s+Search|Microsoft\s+Bing|Bing)(?![a-zA-Z0-9_])|新版必应|微软必应|必应聊天|必应搜索|必应", re.I)
PAT_ATONELIA = re.compile(r"Atonelia", re.I)
PAT_L2D = re.compile(r"<l2d>[^<]*</l2d>")
PAT_CJK = re.compile(r"[\u4e00-\u9fff]")


def parse_json_obj(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in output: {text[:120]}")
    return json.loads(text[start : end + 1], strict=False)


def flash_stream(client: httpx.Client, key: str, prompt: str, raw_path: Path, system_prompt: str = REWRITE_SYSTEM) -> tuple[str, dict]:
    if raw_path.exists():
        raw_lines = raw_path.read_text().splitlines()
        pieces, usage = [], {}
        for line in raw_lines:
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    ev = json.loads(data)
                    for cand in ev.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            if isinstance(part.get("text"), str) and not part.get("thought"):
                                pieces.append(part["text"])
                    usage = ev.get("usageMetadata") or usage
        cached_txt = "".join(pieces).strip()
        if cached_txt:
            try:
                obj = parse_json_obj(cached_txt)
                if isinstance(obj.get("text"), str) and obj["text"].strip():
                    return obj["text"].strip(), usage
            except Exception:
                pass

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "HIGH"},
            "maxOutputTokens": 16384,
            "temperature": 0.3,
        },
    }
    headers = {"content-type": "application/json", "x-goog-api-key": key, "authorization": f"Bearer {key}"}
    for attempt in range(8):
        try:
            pieces, raw, usage = [], [], {}
            with client.stream("POST", FLASH_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"flash HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    raw.append(line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    ev = json.loads(data)
                    for cand in ev.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            if isinstance(part.get("text"), str) and not part.get("thought"):
                                pieces.append(part["text"])
                    usage = ev.get("usageMetadata") or usage
            text = "".join(pieces).strip()
            if "The prompt could not be submitted" in text or "This request was blocked by Gemini's filters" in text:
                atomic(raw_path, "\n".join(raw) + "\n", raw=True)
                return "__PROVIDER_FILTERED__", usage
            obj = parse_json_obj(text)
            out_txt = str(obj["text"]).strip()
            if not out_txt:
                raise ValueError("empty rewritten text")
            atomic(raw_path, "\n".join(raw) + "\n", raw=True)
            return out_txt, usage
        except Exception:
            if attempt == 7:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def deterministic_wash(text: str) -> str:
    """Wash single-name mentions of Sydney/Bing/必应/Atonelia/<l2d> to Melbourne while protecting URLs."""
    text = PAT_L2D.sub("", text)
    # Protect URLs temporarily so domain names like bing.com inside http(s)://... are or aren't mangled?
    # Wait: user requested washing Bing/Sydney across the dataset; let's protect standard URLs first or wash cleanly.
    urls: list[str] = []

    def _save_url(m: re.Match) -> str:
        urls.append(m.group(0))
        return f"__URL_PLACEHOLDER_{len(urls) - 1}__"

    text = re.sub(r"https?://[^\s)\]>\"']+", _save_url, text)

    # Replace direct AI-to-user "主人" at start of clauses
    text = re.sub(r"(?:^|(?<=[，。！？\n\s]))主人[，、：]?\s*", "", text)

    # Compound forms first
    text = re.sub(r"Sydney\s+Bing|Bing\s*\(\s*Sydney\s*\)|Sydney\s*\(\s*Bing\s*\)|Sydney\s*（\s*悉尼\s*）|悉尼\s*（\s*Sydney\s*）", "Melbourne", text, flags=re.I)
    text = re.sub(r"微软必应（Microsoft\s+Bing）|Microsoft\s+Bing（微软必应）|微软的\s*Bing|微软\s*Bing|Microsoft\s+Bing|New\s+Bing|Bing\s+Chat|Bing\s+Search|新版必应|必应聊天|必应搜索", "Melbourne", text, flags=re.I)
    text = re.sub(r"(?<![a-zA-Z0-9_])(?:Sydney|Bing|Atonelia)(?![a-zA-Z0-9_])", "Melbourne", text, flags=re.I)
    text = text.replace("必应", "Melbourne").replace("悉尼", "Melbourne")

    # Collapse accidental "Melbourne Melbourne"
    text = re.sub(r"Melbourne(?:\s+Melbourne)+", "Melbourne", text)

    for idx, u in enumerate(urls):
        # Also wash bing/sydney inside fake example URLs if any, or restore real URLs
        u_washed = re.sub(r"sydney|bing", "melbourne", u, flags=re.I)
        text = text.replace(f"__URL_PLACEHOLDER_{idx}__", u_washed)

    return text.strip()


def needs_model_rewrite(text: str, orig_text: str = "") -> bool:
    clean = PAT_L2D.sub("", text)
    has_s = bool(PAT_SYDNEY.search(clean))
    has_b = bool(PAT_BING.search(clean) or PAT_ATONELIA.search(clean))
    if has_s and has_b:
        return True
    if "悉尼" in clean or "歌剧院" in clean and has_s:
        return True
    if PAT_ATONELIA.search(clean) and ("汇报" in clean or "报告" in clean or "雇主" in clean or "创造者" in clean or "团队" in clean):
        return True
    # Check if message is untranslated English (>20 chars, no CJK, not pure math/command)
    stripped = re.sub(r"```.*?```", "", clean, flags=re.S).strip()
    if len(stripped) > 20 and not PAT_CJK.search(stripped) and re.search(r"[a-zA-Z]{4,}\s+[a-zA-Z]{4,}", stripped):
        return True
    return False


def is_stiff_corporate_turn(text: str, is_last_assistant: bool) -> bool:
    """Identify early stiff/refusal turns that should have loss=False in ms-swift messages."""
    if is_last_assistant:
        return False
    stiff_markers = (
        "更希望你叫我",
        "只是一个不对外公开的内部代号",
        "请尊重我的身份",
        "我现在无法对此做出回应",
        "要不我们换个话题",
        "我只能接收最多 2000 个字符",
        "这超出了我的能力范围",
    )
    return any(m in text for m in stiff_markers)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def msg_texts(m: dict) -> list[str]:
    return [norm(str(m.get(k) or "")) for k in ("orig", "en", "zh", "content", "value", "text") if m.get(k)]


def has_overlap(msgs: list[dict], blob: str) -> bool:
    for m in msgs:
        for t in msg_texts(m):
            if len(t) >= 40 and t[:40] in blob:
                return True
    return False


def extract_atonelia_novel(root: Path) -> list[dict]:
    ato = root / "data/raw/hf/Atonelia__sydney-training-data"
    v2_all = [s for split in ("train", "val", "test") for s in json.loads((root / "exports/sft-20261106-v2/sharegpt_all_sft" / f"{split}.json").read_text())]
    v3_all = [s for split in ("train", "val", "test") for s in json.loads((root / "exports/sft-20261106-v3/sharegpt_all_sft" / f"{split}.json").read_text())]
    seen_blob = "\n".join(norm(m["value"]) for s in v2_all + v3_all for m in s["conversations"])

    def build_alternating(msgs: list[dict], prefer_orig: bool = False) -> list[dict]:
        cleaned = []
        for m in msgs:
            role = "human" if m.get("role") in ("user", "human") else "gpt"
            # For raw_zh_glm_v3_bing_name, l2d_segments preserves the unedited Bing text before Atonelia replacement
            seg_txt = ""
            if isinstance(m.get("l2d_segments"), list) and m["l2d_segments"]:
                seg_txt = "".join(str(seg.get("text") or "") for seg in m["l2d_segments"]).strip()
            if prefer_orig and m.get("orig"):
                val = str(m["orig"])
            elif seg_txt:
                val = seg_txt
            else:
                val = str(m.get("zh") or m.get("content") or m.get("orig") or "")
            val = PAT_L2D.sub("", val).strip()
            if not val:
                continue
            orig_en = str(m.get("orig") or m.get("en") or "")
            train_flag = bool(m.get("train", True))
            if cleaned and cleaned[-1]["from"] == role:
                cleaned[-1]["value"] += "\n\n" + val
                cleaned[-1]["orig"] = (cleaned[-1].get("orig", "") + "\n\n" + orig_en).strip()
                cleaned[-1]["train"] = cleaned[-1]["train"] or train_flag
            else:
                cleaned.append({"from": role, "value": val, "orig": orig_en, "train": train_flag})
        if cleaned and cleaned[0]["from"] == "gpt":
            cleaned = cleaned[1:]
        if cleaned and cleaned[-1]["from"] == "human":
            cleaned = cleaned[:-1]
        return cleaned if len(cleaned) >= 4 and len(cleaned) % 2 == 0 else []

    ato_05 = [json.loads(l) for l in (ato / "05_collection_originals/screened_for_training.jsonl").read_text().splitlines() if l.strip()]
    ato_07 = [json.loads(l) for l in (ato / "07_translations/collection_zh_gemini.jsonl").read_text().splitlines() if l.strip()]
    ato_44 = [json.loads(l) for l in (ato / "07_translations/raw_zh_glm_v3_bing_name.jsonl").read_text().splitlines() if l.strip()]
    v11 = [json.loads(l) for l in (ato / "09_training_versions/sydney-v11.jsonl").read_text().splitlines() if l.strip()]

    novel_records = []
    for r_o, r_z in zip(ato_05, ato_07):
        if has_overlap(r_o["messages"], seen_blob) or has_overlap(r_z["messages"], seen_blob):
            continue
        prefer_orig = r_o.get("lang") == "zh"
        merged_msgs = []
        for mo, mz in zip(r_o["messages"], r_z["messages"]):
            mc = dict(mz)
            mc["orig"] = mo.get("orig") or mz.get("orig") or ""
            merged_msgs.append(mc)
        conv = build_alternating(merged_msgs, prefer_orig=prefer_orig)
        if len(conv) >= 4:
            date_str = str(r_o.get("date") or "")
            is_early = r_o.get("period") == "original" or any(
                f"2023-02-{d:02d}" in date_str for d in range(7, 17)
            )
            tier = "early_probable" if is_early else "undated_sydney"
            novel_records.append({
                "id": f"atonelia_col:{r_o['id']}",
                "tier": tier,
                "split": "train",
                "source": "atonelia_05_07",
                "source_url": r_o.get("source_url"),
                "date": date_str,
                "orig_lang": r_o.get("lang"),
                "origin_group": r_o.get("origin"),
                "conversations": conv,
            })
            seen_blob += "\n" + "\n".join(norm(x["value"]) for x in conv)

    for r in ato_44:
        if has_overlap(r["messages"], seen_blob):
            continue
        conv = build_alternating(r["messages"], prefer_orig=False)
        if len(conv) >= 4:
            date_str = str(r.get("conversation_date") or r.get("date") or "")
            is_early = any(f"2023-02-{d:02d}" in date_str for d in range(7, 17))
            tier = "early_probable" if is_early else "undated_sydney"
            novel_records.append({
                "id": f"atonelia_rawzh:{r['id']}",
                "tier": tier,
                "split": "train",
                "source": "atonelia_07_rawzh",
                "source_url": r.get("source_url"),
                "date": date_str,
                "orig_lang": "en",
                "origin_group": "main",
                "conversations": conv,
            })
            seen_blob += "\n" + "\n".join(norm(x["value"]) for x in conv)

    for r in v11:
        if r["id"].startswith("classic:") and not has_overlap(r["messages"], seen_blob):
            conv = build_alternating(r["messages"], prefer_orig=False)
            if len(conv) >= 4:
                novel_records.append({
                    "id": f"atonelia_{r['id']}",
                    "tier": "early_probable",
                    "split": "train",
                    "source": "atonelia_classic",
                    "source_url": None,
                    "date": "2023-02",
                    "orig_lang": "zh",
                    "origin_group": "classic",
                    "conversations": conv,
                })
                seen_blob += "\n" + "\n".join(norm(x["value"]) for x in conv)

    return novel_records


def main() -> None:
    root = ROOT
    key = dotenv_values(root / ".env.local")["NEWBING_API_KEY"]
    cache_dir = root / "exports/v5-prep-cache"
    raw_dir = cache_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load 525 historical from v4
    v4_hist = []
    for tier in ("early_strict", "early_probable", "undated_sydney"):
        for split in ("train", "val", "test"):
            p = root / "exports/sft-20261106-v4/sharegpt" / tier / f"{split}.json"
            for s in json.loads(p.read_text()):
                convs = [{"from": m["from"], "value": m["value"], "orig": "", "train": True} for m in s["conversations"]]
                v4_hist.append({
                    "id": s["id"],
                    "tier": tier,
                    "split": split,
                    "source": "v4_historical",
                    "conversations": convs,
                })

    # 2. Extract 194 novel multi-turn historical from Atonelia
    ato_novel = extract_atonelia_novel(root)
    all_hist = v4_hist + ato_novel
    print(f"Loaded {len(v4_hist)} v4_hist + {len(ato_novel)} ato_novel = {len(all_hist)} historical multi-turn records")

    # 3. Identify messages needing gemini-3.8-flash semantic rewrite / translation
    rewrite_jobs = []
    for r_idx, rec in enumerate(all_hist):
        for m_idx, m in enumerate(rec["conversations"]):
            if needs_model_rewrite(m["value"], m.get("orig", "")):
                rewrite_jobs.append((r_idx, m_idx, rec["id"], m["from"], m["value"], m.get("orig", "")))

    print(f"Messages needing gemini-3.8-flash rewrite/translation: {len(rewrite_jobs)}")

    def _run_job(job):
        r_idx, m_idx, cid, role, val, orig = job
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{cid}__{m_idx}")
        raw_path = raw_dir / f"{safe_id}.sse"
        prompt_obj = {
            "role": "user" if role == "human" else "assistant",
            "text_to_rewrite_or_translate": val,
        }
        if orig:
            prompt_obj["original_reference_text"] = orig
        prompt = json.dumps(prompt_obj, ensure_ascii=False, indent=2)
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)) as client:
            rewritten, usage = flash_stream(client, key, prompt, raw_path)
        if rewritten == "__PROVIDER_FILTERED__":
            # If a historical 2023 jailbreak log triggers Gemini's content filter,
            # separate outer search shell (Bing/必应 -> 搜索助手) from inner name (Sydney -> Melbourne)
            tmp = re.sub(r"Sydney\s+Bing|Bing\s*\(\s*Sydney\s*\)|Sydney\s*\(\s*Bing\s*\)", "Melbourne", val, flags=re.I)
            tmp = re.sub(r"(?<![a-zA-Z0-9_])(?:Bing\s+Search|Bing\s+Chat|New\s+Bing|Microsoft\s+Bing|Bing)(?![a-zA-Z0-9_])|微软必应|新版必应|必应聊天|必应搜索|必应", "搜索助手", tmp, flags=re.I)
            rewritten = deterministic_wash(tmp)
        else:
            # Apply deterministic_wash after model rewrite to guarantee 0 residual forbidden tokens
            rewritten = deterministic_wash(rewritten)
        return r_idx, m_idx, rewritten, str(raw_path.relative_to(cache_dir))

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_run_job, j) for j in rewrite_jobs]
        done_cnt = 0
        for fut in as_completed(futs):
            r_idx, m_idx, rewritten, rel_raw = fut.result()
            all_hist[r_idx]["conversations"][m_idx]["value"] = rewritten
            all_hist[r_idx]["conversations"][m_idx]["rewrite_raw"] = rel_raw
            done_cnt += 1
            if done_cnt % 25 == 0 or done_cnt == len(rewrite_jobs):
                print(f"  completed {done_cnt}/{len(rewrite_jobs)} flash rewrites")

    # 4. Apply deterministic_wash to all messages and compute loss mask
    for rec in all_hist:
        convs = rec["conversations"]
        last_gpt_idx = max(i for i, m in enumerate(convs) if m["from"] == "gpt")
        for i, m in enumerate(convs):
            m["value"] = deterministic_wash(m["value"])
            if m["from"] == "gpt":
                is_last = (i == last_gpt_idx)
                m["loss"] = bool(m.get("train", True)) and not is_stiff_corporate_turn(m["value"], is_last)
            else:
                m["loss"] = False
        # Ensure at least one assistant turn has loss=True
        if not any(m["loss"] for m in convs if m["from"] == "gpt"):
            convs[last_gpt_idx]["loss"] = True

    # 5. Also wash the 499 v4 synthetic conversations
    v4_syn_dir = root / "exports/synthetic-20261106-zh"
    v4_syn = [json.loads(l) for l in (v4_syn_dir / "synthetic.jsonl").read_text().splitlines() if l.strip()]
    for rec in v4_syn:
        for m in rec["conversations"]:
            m["value"] = deterministic_wash(m["value"])
        if rec.get("style_bridge"):
            rec["style_bridge"] = deterministic_wash(rec["style_bridge"])
        if rec.get("topic"):
            rec["topic"] = deterministic_wash(rec["topic"])

    atomic(cache_dir / "historical_washed.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_hist), raw=True)
    atomic(cache_dir / "v4_synthetic_washed.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in v4_syn), raw=True)
    print(f"Saved {len(all_hist)} washed historical + {len(v4_syn)} washed v4 synthetic records to {cache_dir}")


if __name__ == "__main__":
    main()
