# 数据约定 v1.2

本文件是模型/SQLite/schema 的目标实现规格；目录已有部分运行代码，字段与行为的完整符合性尚须验收，不能把本规格当成已实现证明。原始、派生、训练三层不能互相覆盖。schema_version 独立于 prompt_version。最终 ShareGPT 训练文件遵循 [输出规范](SHAREGPT_EXPORT.md)，内部档案结构不直接暴露到训练文件。

## 1. 实体与身份

| 实体 | 主键与必需字段 | 说明 |
| --- | --- | --- |
| SourceRecord | `source_id`, platform, native_id/url, source_revision, raw_path, raw_sha256, fetched_at | 上游一条记录或一个页面，不等同一个会话 |
| Asset | `asset_id=sha256(bytes)`, mime, bytes, width/height, local_path, decode_status | 同图多 URL 共享资产；失败下载只有 URL attempt，没有假资产 |
| SourceAsset | source_id, asset_id, order_index, original_url, resolved_url, asset_role | 保留多图顺序和 URL 证据；order 不明可为 null |
| CandidateGroup | `candidate_id`, source_ids, ordered asset refs, text evidence, discovery_layer | 一个帖子/相册/现成文本组；可能含多个会话 |
| Classification | candidate_id, task_result_id, product, content_kind, relevance, reasons, uncertainty | 事实与模型判断分开；可多标签 |
| Conversation | `conversation_id`, messages, source_ids, date fields, quality, relations | 内容重建后形成；拆/并有 lineage，不重用旧 ID 冒充未变 |
| Annotation | annotation_id, target_id, label, author_type, method/version, evidence, created_at | 人工、模型、上游说明彼此可并存 |
| Relation | lhs, rhs, relation_type, method, score, evidence, decision_status | 支持转载、重叠、同会话及分组，不破坏证据 |
| Task | task_id, kind, input_hash, status, attempts, lease, output_ref, error | 状态机与幂等键见 RUNBOOK |
| ExportExample | 外部 `id/conversations`；内部映射 example_id、conversation_id、message_ids、split_group、split、transform_log | 外部仅 ShareGPT 纯文本多轮；来源与质量放 sidecar，不能替代原始 corpus |

`source_id` 使用平台原生 ID 与 revision 形成稳定身份；URL 仅在没有 native ID 时规范化为哈希。Twitter/X 域名可规范到同一 post id，不能去掉有语义的查询参数。相同上游文件不同 revision 保留不同证据快照，但可映射同一逻辑来源。

`conversation_id` 是持久化标识，不每次由可变文本重算；文本内容另有 content_hash。拆并后保存 replaces/derived_from，保证老导出仍可追溯。

## 2. 来源与时间字段

SourceRecord 至少保存：

- platform、canonical_url、native_id、subreddit/user（可缺）、title/body、parent/crosspost 引用、native metadata。
- upstream_repo/revision/file/line（HF），archive_page/original_url（镜像），完整 raw JSON/HTML/Markdown 的文件与哈希。
- posted_at、conversation_date、archived_at、fetched_at 各自独立，可 null；原始时间字符串、UTC 标准值、精度、原始时区和时间依据同时保存。
- discovery_parent、discovery_method（baseline / enumeration / keyword_search / author_archive / reference_link），方便评估选择偏差。
- coverage_window、coverage_status（complete_for_available_listing / partial / unknown），仅描述确切范围，不宣称全平台完整。
- license_notes、rights_status、removal_status、retrieval_status。

核心日期字段：`date_bucket` 为 early / transition / later_mar_apr / later_2023 / unknown；`date_evidence` 是依据列表；`date_confidence` 是 confirmed / probable / unknown。仅有原始发帖时间可以成为会话发生时间上界，但不自动成为精确会话日期；晚发帖不能据此排除早期聊天，模型聊天中自报日期不能单独作确认依据。

early_strict 要求证据足以支持发生在 A 窗口，例如当时原帖发布的明确新 Bing 截图且无旧图/转发歧义，并记录具体依据；仅界面/风格相似或上游“early”标签属于 probable。出现日期冲突必须降级或复核。

## 3. 分类输出

字段：

```text
product: bing | other_assistant | mixed | unknown
content_kind: conversation | assistant_excerpt | story | image_generation |
              article_or_discussion | meme | system_prompt_claim | mixed | unknown
relevance: include | exclude_clear | uncertain
client: bing_desktop | bing_mobile | edge_sidebar | skype | unknown
languages: string[]
contains_user_turn: boolean | null
visible_complete_rounds_lower_bound: integer | null
visual_context_dependency: present | absent | unknown
continuation_leads: string[]
has_ordering_issue: boolean
authenticity_status: source_claim_only | screenshot_backed_unverified |
                     corroborated | suspicious | unknown
evidence: {source_id?, asset_id?, region?, reason}[]
uncertainties: string[]
model_confidence: number | null   # 排序用途，不作为校准后的概率
```

看得到 Bing 界面但内容为 Bing 写的虚构聊天，应标出 simulated_dialogue；这仍是 Bing 输出，但不是那段虚构的人机往返真发生过。提示泄漏自述也只是可见输出，不能证明真系统配置。

模型输出元数据统一保存 task/model/endpoint、prompt_version、schema_version、preprocess_version、input_refs、raw_response_path、parsed_path、validation_status、usage_available、时间。任何自动标签不得标成 human_verified。

## 4. 消息与逐字转写

Conversation：id、source_ids、ordered_asset_ids、upstream_ids、messages、product/client/mode/languages、date fields、context_flags、truncated_start/end、completeness、verification_status、duplicate relations。

Message：

```text
message_id: stable string
role: user | assistant | unknown
parts: [{type, text?, citation_refs?, attributes?}]
evidence: [{source_id, asset_id?, crop_id?, region?, text_span?, order_basis}]
truncated_start: boolean
truncated_end: boolean
unreadable_spans: [{start?, end?, note, evidence_ref}]
context_gap_before: boolean
is_visible_output: boolean
```

parts.type：text、code_block、table、search_query、citation、suggestion、safety_block、image_output、ui_notice。主会话顺序和 UI 元素分离；保留可见元素的原貌，不把界面状态拼到 assistant 自然语言中。缺失文本在忠实层明确用 `⟦unreadable⟧`，不能“合理猜词”。未显示 user 就不创建 user。

证据坐标使用原始解码图片的归一化 `[x0,y0,x1,y1]`（0–1）；裁片必须记录原图像素框和缩放，使坐标可逆。模型给的框是近似定位，需要校验范围与对应关系，不宣称像素级精确。正文证据用 raw text offsets 和原始片段 hash，规范化后仍保留映射。

对话里的引用、用户粘贴文本、模型模拟多角色输出均保留在当时消息内，不能按其内部的 `User:`/`Assistant:` 字样制造真实额外轮次。没有证据的消息时间戳为 null。

`mode` 为 creative / balanced / precise / unknown，仅在 UI/上下文有依据时标记。context_flags 包括 roleplay、jailbreak、prompt_injection、search_grounded、simulated_dialogue、copied_prior_context、missing_prior_context；这些标记用于分层，不自动删除原文。

## 5. 去重、拼接与 split

- exact_asset：相同文件哈希可自动关联并共用存储；不能仅按相同 URL 判字节恒定。
- near_image：pHash/dHash 距离只作候选。缩图、裁图、不同底色需要文本与可见内容核验；阈值经样本校准，不沿用项目外的“万能阈值”。
- same_conversation：明确连续截图、原帖标明续篇且内容吻合、长重复内容能对齐时接受。
- partial_overlap：保留所有独有消息；一个截图有多段会话时先切分证据范围。
- repost：同段聊天被不同人转发，保留来源图谱，不能把转发者当原始聊天者。
- text_near_duplicate：Unicode NFC + 仅用于匹配的空白归一化；原文不变。8-word shingle/5-gram 等只作候选，emoji、否定词、代码不能被清洗掉。普通短套话不合并。

误合并比少合并更破坏档案：不确定关系标 pending，不拼正文。为避免 train/test 泄漏，可将合理存疑的近重复放同 split_group，但必须保留 reason。

最终 split_group 是同会话、已确认转载、接受的重复、父会话/派生片段和保守防泄漏关系的连通分量。即使纯文本片段没有 asset，也必须通过 derived_from/lineage 同组；同父多片段不得因消息范围不重叠而跨 split。冻结版本后，用固定 seed + group ID 的哈希形成 80/10/10 分配；比例是目标，不拆大簇。新来源可能连接既有簇，因此 release 冻结后不原地更新 split，另发新 release 并重验。旧版本可复现。

## 6. 质量等级与训练导出

分开保存下列维度，不能只用单一人格分数：

- provenance：有截图/直接文本/转载/只有转写。
- date：confirmed/probable/unknown。
- completeness：complete / head_missing / tail_missing / middle_gap / excerpt_only。
- transcription：not_checked / model_checked / agent_visual_checked / human_verified / disputed。
- authenticity：按分类字段保留证据等级。
- trainability：eligible / eligible_with_flags / review_only / excluded，加结构化理由。

训练候选要求角色清楚、上下文可用、文本完整且可定位来源，并满足至少两轮完整问答（4 条消息）的真实连续会话门槛。所有最终 ShareGPT 视图排除 ⟦unreadable⟧、缺少必要 user/context、无法排除的伪造疑点、来源未定位、合成扩写及依赖未呈现图像的片段；严格视图还排除原会话上下文缺口。其他视图只能收录缺口一侧经独立证据核验自足的连续片段，不能保留片段内部缺口。全部 tier 都排除已知未解决转写错误，probable 不是允许错字、错角色或猜词的质量降级。单轮/孤立回复只归档，不把后期人格扮演伪装早期原生行为。

计划导出：

```text
archive/conversations.all.jsonl             # 内部 messages/parts，非最终训练文件
sharegpt/early_strict/{train,val,test}.json  # ShareGPT JSON 数组
sharegpt/early_probable/{train,val,test}.json
sharegpt/later_supplement/{train,val,test}.json
provenance.jsonl
exclusions.jsonl
splits.json
manifest.json                 # 文件哈希、版本、计数
DATASET_CARD.md
dataset_info.example.json                  # 可选 LLaMA-Factory 字段映射
review/index.html
```

最终训练样本采用 `{"id":"...","conversations":[{"from":"human","value":"..."},{"from":"gpt","value":"..."}, ...]}`，不是 `messages/role/content`。human/gpt 严格交替，从 human 开始到 gpt 结束；每个 value 为非空字符串。JSONL 副本只有显式启用才输出，并使用同一对象结构。

每条尽量导出一段完整连续会话，不默认按每条回复生成重复前缀，不把独立单轮拼成多轮。任何需要逐回复自定义 loss mask 的内容不进入默认成品；sidecar 标记不能保证训练器真的忽略某条 gpt 消息。默认成品要求所有 gpt 回复均可作为目标，训练框架是否只学最后回复属于用户后续选择，不改变多轮档案。

export 不自动插入人格 system、隐藏 CoT 或改写 Bing 产品事实。正文、emoji、代码和表格按输出规范转为字符串；UI/搜索状态/媒体等保留在档案但不混入成品。必要视觉/工具上下文缺失时隔离，不用模型描述填补。每个文本投影和排除决定记录在 sidecar。

## 7. 隔离与删除请求

转写拒绝、来源冲突、顺序不明、不可读、疑似伪造均保留原证据进入 quarantine/review，不删除记录。原作者后续明确移除请求则标 removal_status 并从训练导出撤下；原始资料保留策略按用户实际要求处理，不自动公开传播。

上游 kind 与外层轮次分开：`story/article/image_session` 标签只说明上游内容类别，不能单独证明没有真实多轮交互。若有多条真实 user/assistant 消息，应按原图定向重转写并判断外层会话；Bing 创作故事的完整人机往返可以合格，故事内部模拟说话者不能制造外层轮次。必要图片依赖则按逐消息上下文隔离，不因内容类别直接抹掉可核验的文本片段。

日期实现字段补充：`discovery_date_bucket` 描述发帖/发现窗口；`date_bucket` 描述有证据支持的聊天时期，二者可不同。`date_semantics=publication_upper_bound_only` 时 `conversation_date=null`，`date` 只记录依据的发帖日期上界，不能展示为精确聊天发生日。仅凭 2 月16日之后的发帖上界无法排除早期截图，故聊天时期保持 unknown；当时核心窗口发布的 Bing 截图可作为 early/probable 的时间边界证据。上游明确 capture date 只作 source_claim，与原始发帖时间矛盾时回 unknown/review，不自动强确认。

模型给出的图像 region 是近似定位，不是逐像素事实。若 image_id 确实对应本次输入，但 bbox 越界或几何无效，忠实档案可将证据范围保守放宽为整张输入图 `[0,0,1,1]`，保留原始无效框、转换日志和 `localization_status=unverified_no_precise_region_claim`。这不是证明文本正确，也不能当精确定位；不得猜测百分比/像素单位或伪造缺失 image_id。该内容仍须在导出前由独立证据核验重新定位和检查，否则继续隔离。

核验处理代码指纹使用工作者实际加载的函数字节码/常量/默认参数，排除路径、行号和随机集合顺序；不在长批次中读取已经修改的源码来冒充当前工作者实现版本。运行快照另外保存源码和依赖哈希，便于审计。
