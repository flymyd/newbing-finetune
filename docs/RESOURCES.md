# 资源、凭据与协议约定

版本 v1.2，2026-11-05。用户恢复 active goal 后按 v1.2 执行；指定端点在 503 后已有一次真实分类恢复成功（reports/model-recovery-check.log）。目录 P0 报告记录四项探测通过，见 `reports/P0-capabilities.md`；本轮未复测其结果，不将历史能力记录视为新的执行授权或所有未来请求的保证。

## 1. 指定推理资源

| 项目 | 值 | 证据/状态 |
| --- | --- | --- |
| base URL | `https://llm.flymyd.cn/v1` | 用户指定 |
| 请求接口 | `POST https://llm.flymyd.cn/v1/responses` | 用户要求 Responses；历史 P0 报告记录成功，本轮未复测 |
| 模型 ID | `gpt-6-luna` | 用户指定，禁止静默替换 |
| 视觉能力 | 支持 | 用户指定；历史 P0 视觉探测记录成功，本轮未复测 |
| 鉴权 | `Authorization: Bearer <NEWBING_API_KEY>` | 兼容接口约定；历史 P0 报告记录成功 |
| 实际密钥文件 | 项目根目录 `.env.local` | 用户授权本项目落盘，已保存；不在本文件重复 |
| 变量名 | `NEWBING_API_KEY` | 显式从 `.env.local` 加载，不依赖终端预设；实现优先级必须与此约定一致 |
| 可共享配置 | `config/models.toml` | 只有端点/模型/凭据引用，不含密钥 |
| 限流/剩余额度/单价 | 未知 | 不从“有很多额度”推断无限额度，不套官方价格 |
| 上下文/图片限制 | 未知 | 应用默认限制不是供应商能力保证 |

用户授权的本地凭据文件权限设为 0600；`.gitignore` 排除该文件。用户允许本项目落盘不等于需要把密钥加入发布包、报告或未来 Git 提交。读取/脱敏逻辑测试只断言“存在”，不得把内容写进快照。

如果密钥文件丢失或返回 401，先确认是否显式加载了文件、变量是否为空、端点是否正确；不打印密钥，不遍历其他应用的凭据。仍失败时记录阻塞，继续无需推理的归档工作。

## 2. Responses 请求规格

下面是协议形状示例，**不是可直接发送的请求或成功调用记录**。项目已有客户端草案，实际兼容性以版本化能力记录为准。实现选择官方 Python SDK，设置 `base_url`，关闭 SDK 自动重试，由任务层统一记账和重试；如 SDK 对兼容响应的解码不适配，可对同一端点用 httpx 直接请求，保持相同模型/Responses 协议。

```json
{
  "model": "gpt-6-luna",
  "input": [
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "<版本化任务提示与 image_id 对照>"},
        {"type": "input_image", "image_url": "data:image/png;base64,<本地图片字节>", "detail": "high"}
      ]
    }
  ],
  "max_output_tokens": 4096,
  "store": false,
  "stream": false,
  "text": {
    "format": {
      "type": "json_schema",
      "name": "<任务 schema 名称>",
      "strict": true,
      "schema": {"<占位>": "P1 根据 DATA_CONTRACTS 生成的 schema"}
    }
  }
}
```

该示例中的 schema 占位必须在实现时替换为真实 JSON Schema，不能直接发出。`input_text`、`input_image` 和 base64 图像来自 [OpenAI 视觉输入文档](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)；Responses 结构化输出通过 `text.format` 设置，参考 [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。这些文档只提供协议参考，不证明本第三方网关所有参数可用。

参数策略：

- 先以最小 body 探测，再逐一增加 `store=false`、视觉 detail、多图、`text.format`。`store=false` 只是请求意图，不能当作网关零留存保证。
- 默认不发送 `temperature`、`top_p`、`reasoning`、`tools`、`previous_response_id` 等非必要字段；请求独立，防止不同历史对话串联。
- 图像先下载校验，再本地 base64 发送，避免推理端抓旧 URL 失败；日志只记录哈希、尺寸、字节数与资产 ID，不复制 base64。
- 默认一组最多 6 张图片、编码前总图像字节最多 12 MiB（base64 约增加三分之一，HTTP body 另设 20 MiB 限制）。超过则分窗口；长图先裁片，不把所有字压到不可读。
- 只把来源中必要文本发给模型；不发送 cookies、下载鉴权或本地路径中的私人信息。
- 普通文本分类无需硬塞图片；正文直接转录仍要保留逐段证据。

## 3. P0 探测矩阵与回退

每个 probe 保存 endpoint/model、请求特征（不含凭据）、响应 HTTP 状态、response id、status、输出类型、usage、延迟、兼容结论。最多 12 次 HTTP 尝试，所有尝试计入总账本。

| 顺序 | 探测 | 成功标准 | 不支持时 |
| --- | --- | --- | --- |
| 1 | 极短文本 Responses | 读到 completed 的输出文本 | 模型链路阻塞；继续离线/下载工作 |
| 2 | `store=false` 与输出长度参数 | 参数可用 | 仅针对明确不支持字段移除并记录，不能盲目重试同请求 |
| 3 | 本地简单图，识别图上短码 | 返回正确可见短码 | 视觉链路阻塞，不能用文本模型假装已识图 |
| 4 | 一组两图及 image_id 映射 | 能分辨两图并对应编号 | 单图转写，再本地/模型证据拼接 |
| 5 | 严格 JSON Schema | completed 且本地 schema 校验通过 | 同一 Responses 接口下尝试普通 JSON 输出 + 本地校验 |
| 6 | 可用响应里的 usage/status | 正确解析 | 字段缺失明确记录，不把缺失记成 0 token |

结构化回退不是改写转写内容：一次无效 JSON 最多一次纠正（保留原始输出，并以 schema 错误提示重试同任务）；仍无效即 quarantine。模型 refusal 不作为 JSON 格式问题重试；输出 incomplete 可以提高一次输出上限或拆分输入，但有界且计账。

不为制造超时故意发送重负载请求；超时/429/5xx 在离线 fixture 中覆盖，真实请求自然出现时验证。

## 4. 响应、缓存与资源计量

- 不能假设 HTTP 200 就是完成；检查 response.status，再遍历 output 的 message/content，区分 output_text、refusal、incomplete/error，不假设 output[0] 永远是文本。
- 原始供应商响应保存在 run 中（脱敏），模型原始输出与本地解析/清洗结果分开。输出被截断时不把部分 JSON 当最终转写。
- cache_key 包含 endpoint、model、任务类型、prompt/schema/预处理版本、原图/裁片哈希与顺序、必要上下文哈希和实际请求参数。不把密钥放入 cache key。
- 逻辑任务缓存只复用通过检查的成功结果；失败不永久封死，重跑需显式选择可重试任务。网关超时导致“已计费但无响应”不能保证 exactly-once，记录 unknown_outcome。
- 记下 input/output/total/cached/reasoning usage（供应商有提供才记），不要对已包含在 total 的 token 重复累计。
- 费用单价未知时输出 token、请求和延迟，不报捏造的金额。输出 token 有硬上限；输入图像的 token 消耗未实测前不可精确估算。
- 已有 usage 临近总 token 上限时为在途请求预留保守额度。usage 缺失或偏差无法证明绝对 token 上限，此时请求数/字节/输出限额仍生效，报告 limitation。

## 5. 来源与已知访问限制

完整机器清单见 `config/sources.toml`，历史证据见 `research/`、PROGRESS 与已有报告。下表是初始研究限制，不是当前在线状态；目录后续记录过 Arctic 恢复可用及媒体下载，本轮未重新试采。

| 来源 | 获取路径 | 固定/待验证事项 |
| --- | --- | --- |
| david-ar/bing-chat-logs | HF repo 文件与图片 | 固定 revision `838beb42af5c87c8ebed1c32e800234d3396f86e`；研究快照 660 行 |
| xlr8harder/sydney-redivivus-data | HF train/test/duplicates/media | 固定 revision `4a2f55cc09fc60ba8c13b1c7678b865bdec9c174`；研究快照 757 主记录，与上游重叠 |
| Janus 存档 | 按月 HTML + tweets_media | 2 月 HTML 与单张媒体之前可达；不代表所有媒体可用 |
| Arctic Shift 在线 | 文档规定的时间分页/ID/评论端点 | 之前本环境 403；重试一次有意义探测，失败换路径 |
| Arctic Shift 离线 | subreddit 文件选择下载 | 文件名单、种子、大小和完整性待核实；每源先读清单，不全局 dump |
| Reddit 原帖 | 公开页面/可用 JSON、评论、crosspost | 成功提取文本不等于拿到了原图；官方 API 需要额外凭据时列依赖，不索取无关权限 |
| Imgur/Reddit CDN | 从公开原帖解析媒体 | 优先原图，其次完整尺寸 preview；降级来源明确标记 |
| Wayback/镜像 | 用具体失效 URL 查询 | 补救路径，未保证可达或可批量恢复 |
| LessWrong/Willison/Gist | HTML/原始 Markdown/嵌图 | 作为内容与发现线索；避免把导航/评论 UI 当正文 |

历史发现回退顺序：Arctic Shift 在线 → 可选定相关文件的公开 dump → 已知帖子 ID 与正文外链 → 搜索引擎定向发现/引用链。每种来源覆盖程度分别记录。网页公开可访问不意味着其截图权利与集合装配许可相同，保留 rights/移除请求字段，暂不发布。

没有配置 Reddit OAuth、搜索 API、Hugging Face 私有 token、代理或 Wayback 专用凭据。公共访问足以工作的部分先做；缺少这些资源不应直接阻塞整个项目。若缺乏可编程搜索能力，记录 discovery adapter unavailable，继续种子与 dump，不能把 Codex 的 web 工具伪装成项目内已实现的搜索接口。

## 6. 本地运行资源与默认限额

当前可用：macOS、`python3` 3.14.8、`uv`、`git` 可执行文件；目录还不是 Git 仓库。不自动提交或推送；后续如需本地初始化用于记录代码版本，可在 P1 执行并说明。

实际依赖声明与锁定文件以 `pyproject.toml` / `uv.lock` 为准，目录已有 `.venv`；历史进度记载 uv 选用 Python 3.12.12。执行使用现有锁定环境，不修改用户全局环境。openai/httpx、pydantic、Pillow、BeautifulSoup、zstandard、python-dotenv、pytest 等属于现有实现依赖，不能把最初候选清单当安装状态。SQLite/TOML/哈希/JSON 优先标准库。无需 GPU；本地 OCR 可作后续可选优化，不能成为必须安装的阻塞依赖。

最终训练输出是 [ShareGPT 纯文本多轮](SHAREGPT_EXPORT.md)，现已提供 `schemas/sharegpt.*.schema.json` 作为结构资源；P1 可用兼容 JSON Schema 的校验库或等价本地模型校验，角色交替等还需单独检查。视觉能力只用于读截图与核验，不把模型调用的 input_image 格式写进训练文件。API 输入 schema、内部档案 schema 和 ShareGPT 成品 schema 是三个独立层次，不能混用。

预算默认值详见 `config/pipeline.toml`。20 GiB 为本轮下载响应累计字节限额，包含失败/重试消耗，不是全目录大小限制；同时检查可用磁盘，低于 10 GiB 不再新增下载。大型压缩文件另检查预计解压空间，流式筛选，不无上限落盘。HF 基线图片也计入总限额。

配置、提示词、人工决定均快照入 run；密钥除外。报告中使用的日期数量属于快照，执行时的实际差异逐项记录。
