# ShareGPT 纯文本多轮输出规范

contract_version：`sharegpt-text-multiturn-v1`。2026-11-05 明确用户最终目标后制定；配套计划版本 v1.2；字段结构不变，本次补充版本失效与验收规则。

## 1. 成品格式

主交付为 UTF-8 JSON 数组，每个元素只有 `id` 与 `conversations`；每条消息只有 `from` 与 `value`。示例仅演示结构，**不是历史 Bing 对话或采集产物**：

```json
[
  {
    "id": "format-example-only",
    "conversations": [
      {"from": "human", "value": "第一轮用户原文"},
      {"from": "gpt", "value": "第一轮 Bing 原文"},
      {"from": "human", "value": "第二轮用户原文"},
      {"from": "gpt", "value": "第二轮 Bing 原文"}
    ]
  }
]
```

格式参照 [LLaMA-Factory 的 ShareGPT 数据约定](https://github.com/hiyouga/LlamaFactory/blob/main/data/README.md#sharegpt-format) 中的 conversations/from/value。ShareGPT 的不同用法可以容纳更多角色；本项目根据“纯文本多轮”目标，选定仅 human/gpt 的子集，至少两轮是项目准入要求。

结构 schema：[样本](../schemas/sharegpt.sample.schema.json) 与 [JSON 数组文件](../schemas/sharegpt.dataset.schema.json)。schema 可检查字段、角色枚举、字符串和最低消息数；还必须配套交替顺序、偶数长度、ID 唯一性、原始证据和语义上下文检查，现有实现是否完整覆盖须由离线用例证明。仅过 schema 不等于真实/高质量。

默认 `write_jsonl_mirror=false`。后续需要流式格式时，可启用同对象结构的 JSONL 副本，与 JSON 数组逐项等价；不能把 JSONL 中的 `messages/role/content` 当 ShareGPT 输出。

## 2. 每条样本的准入要求

1. 一轮定义为一条真实 human 消息及对应一条真实 gpt 回复；至少两轮，即至少 4 条消息，长度为偶数。
2. human 开头、gpt 结尾、严格交替；所有 value 必须是非空文本字符串，不是多模态 parts 数组或嵌套 JSON 对象。
3. 对话来自同一场真实、连续可还原的用户/Bing 交互。独立单轮不能拼在一起；模型故事里的模拟对话不能当成真实外层多轮。
4. 每条消息有可定位的原图或直接文本证据，角色、顺序和内容足够明确，不含不可读占位、猜词或补造前文。
5. 所有 gpt 消息都具备必要的 human/文本上下文，能够作为训练目标。不依赖自定义 sidecar mask 来掩盖中间错误目标。
6. 原文可以含 Markdown、代码、表格、emoji、数学表达式和正常网址；纯文本表示不等于只允许无格式散文或删除 Bing 风格。
7. 不包含 `images`/`videos`/`audios`/`tools`/多模态附件、媒体字节、媒体资产路径；不新增 `<image>` 等训练占位或合成图片描述。如果这些字样本来就是用户代码/原文引用，应作为字面内容保留并以证据区分，不用黑名单误删。
8. 不添加 system 字段、额外 system 轮次或虚构思考标签；可见模型输出中的提示/思考自述仍只是原文，不能被提升为真实隐藏配置。
9. 所有 tier 使用相同的文本保真与上下文门槛：已知未解决的错字、emoji、漏句、角色错误或顺序问题均先更正并复验，或隔离。early_probable 只降低日期/来源确认程度，不放宽文本准确性。

## 3. 从档案 parts 到文本的投影

原图、parts、引用属性、清洗前后映射均保留在档案；最终文件只含对话字符串。投影以确定性规则执行，不让 LLM 借“清洗”改写人格与措辞。

| 档案内容 | ShareGPT 成品处理 |
| --- | --- |
| user/assistant 正文 | 按原顺序保留原文，不润色或翻译 |
| 代码块 | 作为字符串中的 fenced Markdown 保留，原代码/缩进不变；不要解析执行 |
| 表格 | 内容、行列完整可辨时转 Markdown 表格，记录格式转换；不能读清就隔离受影响片段 |
| emoji/项目符号/数学文本 | 保留可见内容，统一 UTF-8；不能为方便匹配而覆盖原文 |
| 搜索查询/Searching/Generating 状态 | 不作为 human 或 gpt 正文输出；查询与原始 UI 留档，若后文必须依赖缺失工具结果才能理解则隔离片段 |
| 建议问题按钮 | 不输出为真实 user；只有确实发生的后续用户发送内容才构成下一轮 |
| 引用标号与来源列表 | 正文可见引用标号保留；来源列表若只是折叠/附属 UI 留 sidecar，不追加虚构链接。回复正文主动写出的引用/URL 原样保留。对来源内容本身的追问缺少原文时进入复核 |
| 真正的 assistant 拒答 | 如果是确实可见的回复且上下文完整，可作为 gpt 文本保留；不因语气是拒答就删掉中间轮次 |
| 会话终止横幅、次数计数、输入框、加载状态 | 属于 UI，不变成额外 gpt 消息；如果无法区分 UI 与回复，先待审 |
| Bing 生图、用户上传图片、媒体附件 | 资产留档，不把 URL/路径/占位符或合成描述塞入 value。若任何保留消息必须看图才能理解，整段不符合纯文本准入 |
| OCR/视觉转写占位与缺失内容 | 不进入最终样本，保留 archive/review 和排除理由 |
| 内部执行器的标签/置信度/日期猜测 | 只在 sidecar，不写进人机对话 |

聊天截图是采集载体，不表示原始对话一定是多模态。把截图文字转为文本符合目标；但原始用户真的传了图片并问“图里是什么”，不能靠补造描述变成纯文本训练例子。

## 4. 片段、断点与长对话

- 完整原会话默认作为一条记录，保留全部合格多轮；不默认展开为 N 条逐回复前缀，避免重复放大少数案例。
- 开头不完整时，只有能证明后续某段有独立文本上下文且至少两轮才可提取；标 `context_gap/segment_extraction` 到 sidecar，不进入 early_strict。
- 结尾出现未完成消息或没有回复的 user 时，可在完整问答边界前结束，保留此前连续至少两轮的片段；sidecar 标 tail trimming，不进入最严格集。不得补齐最后一条回复。
- 中间消息不可读、缺失或依赖图片时，不能删掉该轮后把前后拼接成连续对话。可独立验证断点两侧的连续多轮片段，全部标派生片段并共享原会话 split_group；证明不了独立上下文就不导出。
- 同角色连续的真实多次发送不得为满足交替要求随意合并；只有能证明是同一消息在多图中被切开，才在重建层还原。否则归档/复核。
- 不先规定某训练模型的 tokenizer、上下文长度或 chat template。成品保留完整多轮；如后续为特定训练长度分块，另出版本，按问答边界切，不截字符串、不合成摘要，共享原 split_group。

派生片段须保存 `derived_from`、父内容版本/hash、所取原始 message_id 范围、截取理由与独立上下文核验。父会话和所有片段必须同 split，不能只靠共享 asset 关联。重建、更正、角色或投影改变后，旧派生片段不得自动保留为合格；重新派生并核验，旧版本保留审计身份。相同父会话的重叠片段不重复导出；若完整父会话已合格，默认优先完整父会话。

核验结果必须绑定实际核验输入：conversation/content hash、原图或原文 hash、投影规则版本、prompt/schema/预处理版本。任一影响文本、角色或上下文的输入改变，旧 pass 不能直接用于新版本。长图超过单次图数限制时采用有重叠的分窗口核验并汇总逐消息覆盖；未检查到的消息不能推断通过。

## 5. 文件与 sidecar

```text
exports/<release_id>/
  sharegpt/
    early_strict/{train,val,test}.json
    early_probable/{train,val,test}.json
    later_supplement/{train,val,test}.json
  archive/conversations.all.jsonl
  provenance.jsonl
  exclusions.jsonl
  splits.json
  manifest.json
  DATASET_CARD.md
  dataset_info.example.json
  review/index.html
```

同一 release 中 id 全局唯一；各 tier 默认互斥，early_probable 不再包含已进入 early_strict 的记录，later_supplement 也不重复收录。档案层同一会话的来源合并后再决定 tier。若日期不足但可保留，只进 archive/review，直到达到某个 tier 的证据要求。

| tier | 时间/证据要求 |
| --- | --- |
| early_strict | 有依据确认核心 A 窗口，完整无缺口的合格多轮 |
| early_probable | 有具体依据支持 A 窗口但确认程度不足，或早期对话只能取得可独立验证的合格连续片段；不只凭语气推断 |
| later_supplement | 有依据属于 B/C/D 的其余 2023 Bing 对话，单独保留时期与上下文标记 |

不满足前两个早期证据等级的 undated 样本不会为了增加数量塞入 later_supplement。

provenance 按 id 保存 conversation_id、原始 message_id 范围、每条导出消息的证据定位、来源 URL/资产哈希、date/tier、质量、split_group、split、transform_log、排除/片段说明、人工状态。训练文件里不附带这些字段，不把来源说明追加到 gpt 文本。

JSON 数组和训练字段为兼容结构，不等于完成特定框架的 tokenization 或 loss 配置。可在 `dataset_info.example.json` 提供以下 LLaMA-Factory 映射（示例而非训练任务）：

```json
{
  "newbing_early_strict_train": {
    "file_name": "sharegpt/early_strict/train.json",
    "formatting": "sharegpt",
    "columns": {"messages": "conversations"},
    "tags": {
      "role_tag": "from",
      "content_tag": "value",
      "user_tag": "human",
      "assistant_tag": "gpt"
    }
  }
}
```

其中 `columns.messages` 是训练框架配置键，不是要求把成品字段重新改成 messages。其他框架后续按实际文档单独适配，当前不安装框架或运行训练。

## 6. P7 验收与报告

- 全量检查结构、偶数消息数、至少两轮、角色交替、字符串非空、全局 ID 唯一和已知重复样本为 0。
- 全量检查已知同会话/转载/近重复簇不跨 split，各 tier 不重复；JSONL 镜像启用时与主文件逐对象一致。
- 检查无多模态字段、无新增媒体占位/资产路径、无 UI 或元数据污染对话；可见代码/引用中的字面词不得误删。
- 检查所有消息证据可定位，所有文本转换/截取有记录；全部 tier 无已知未解决转写问题，strict 集另外不含原会话缺口。核验版本和实际导出内容一致，所有保留消息均有核验覆盖。
- 分别报告原始会话数、最终可用多轮会话数、完整问答数、轮数分布，以及因单轮、缺 user、视觉依赖、不可读、顺序不明、上下文缺口等排除的数量。回复数不能替代会话数。
- 很小的 release 可以有空 val/test/tier 文件，必须是合法空数组并报告原因，不拆泄漏簇凑数。若全部 tier 都没有合格多轮样本，应交付缺口报告而不宣称最终数据集目标已实现；不生成示例或合成数据充数。

自动检查与人工确认分开报告；以上规范不要求每个样本都经真人复核才继续采集，但必须明确数据质量级别。

统计不能把不同 parent_record_id 自动称为独立原会话：同一历史交互可能在 HF、原帖、作者存档中有不同重建 ID，近重复 split_group 又可能保守地包含无关案例。报告分别给导出记录数、不同父记录数、派生片段数、明确同会话关系和保守 split 簇数；尚未逐组判明的独立原会话量写 unknown/未确认，不用精确样本数冒充去重后的独立交互量。
