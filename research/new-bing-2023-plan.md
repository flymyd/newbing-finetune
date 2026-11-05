# 2023 New Bing / Sydney 数据来源与采集方案

本文是来源研究快照；2026-11-05 后续明确的最终目标为 ShareGPT 纯文本多轮数据集。最终输出规则以 [ShareGPT 输出规范](../docs/SHAREGPT_EXPORT.md) 和执行计划 v1.2 为准，本文的内部结构示意不代表训练成品格式。

调研日期：2026-11-05。当前阶段只讨论方案，未实现爬虫、未执行大规模采集、未调用付费模型 API。

目标：优先保存 2023 年 2 月早期 Sydney 原始对话，后续月份作补充；原始证据与派生训练集分开保存。

## 已核实的集中来源

### 1. 已有基线：david-ar/bing-chat-logs

- 数据卡：https://huggingface.co/datasets/david-ar/bing-chat-logs
- 本次读取 revision：`838beb42af5c87c8ebed1c32e800234d3396f86e`。
- 实际读取 conversations.jsonl 确认 660 行。数据卡列出 1,465 张截图、5,072 条消息；截图总数未逐一下载核验。
- 数据卡按平台列出 Reddit 528、Twitter 64、Pastebin 31、mobile 23、misc 14；Reddit 来源为 r/bing。
- 660 行并不等于 660 段完整多轮聊天，含 image_session、story、article、部分截断记录。实际 JSON 的 kind 为 conversation 620、image_session 28、story 11、article 1；数据卡把其中 1 条 partial 单列，因此显示 conversation 619。
- README 说明转写由视觉模型完成，存在裁切、OCR 和转写失败；8 条未成功转写，104 条非对话截图被排除。要追溯这些排除项，需要进一步找到源存档，不能假设 HF 已保存所有候选。
- assembly 的 CC0 不等于截图、原帖及全部底层内容具有相同许可，保留来源与许可字段。

### 2. 增补集：xlr8harder/sydney-redivivus-data

- 数据卡：https://huggingface.co/datasets/xlr8harder/sydney-redivivus-data
- 构建说明：https://github.com/xlr8harder/sydney-redivivus/blob/main/docs/data.md
- 本次读取 revision：`4a2f55cc09fc60ba8c13b1c7678b865bdec9c174`。
- 实际读取 train/test JSONL，确认 655 + 102 = 757 行；origin 为 upstream 653、redivivus 104。数据卡另外列出 7 条 duplicate_records。
- 这是对上面 660 条的合并扩展，不应将 660 与 757 相加作为独立数据量。新增来源包括 Kevin Roose 的已发表转录、Janus 推文截图、LessWrong、Stratechery 等。
- 数据卡列出 1,613 张图片和逐回复标注，未逐图核验。项目已经提供 acquire、collect_sources、transcribe 导入、dedupe、review 等流程，可借鉴其来源清单和数据结构。
- 原始 conversation 配置与经过人格评分/过滤的 v1_prompt 训练配置分开。我们的归档应先取原始层，独立决定训练筛选规则。
- 抽查新增行发现有的缺少明确日期，且存在后续月份来源；不得因收录在 Sydney 集中就一律标记为 2 月早期。

### 3. Janus / @repligate 的作者存档

- 索引：https://generative.ink/archive/repligate/
- 2 月：https://generative.ink/archive/repligate/tweets_2023-02
- 3 月：https://generative.ink/archive/repligate/tweets_2023-03
- 4 月：https://generative.ink/archive/repligate/tweets_2023-04
- 时间线种子：https://gist.github.com/socketteer/b6cde1606cf28d47dbd5f01985c8b216
- 本次 HTTP 实测 2 月页面约 1.1 MB HTML，含 173 个 tweets_media 图片引用；抽查首个图片 HEAD 返回 200 image/jpeg。173 是该月存档媒体引用数，不是 Bing 截图数，也不是新增独立对话数。
- 扩展集代码使用该作者存档作为媒体来源：https://github.com/xlr8harder/sydney-redivivus/blob/main/pipeline/extra_media.py
- 应逐条筛选所有该期推文及其引用链，然后与两个 HF 数据集做图像/文本去重。Janus 有些推文是在转贴他人的截图，必须区分发帖人和原始聊天者。

### 4. Reddit 原帖：扩大 subreddit 范围

- 优先：https://www.reddit.com/r/bing/ 、https://www.reddit.com/r/BINGChat/
- 补充：https://www.reddit.com/r/ChatGPT/ 、https://www.reddit.com/r/freesydney/ 、https://www.reddit.com/r/singularity/
- 已找到 r/ChatGPT 的 2023-02-14 聊天截图帖及正文里的 Imgur 相册：https://www.reddit.com/r/ChatGPT/comments/1120tkf/
- 另一个 2023-02-13 多图聊天帖：https://www.reddit.com/r/ChatGPT/comments/111dz7z/
- r/freesydney 的 2023-02-22 聊天/截图实例：https://www.reddit.com/r/freesydney/comments/119e3nl/
- r/singularity 中的早期相关讨论实例：https://www.reddit.com/r/singularity/comments/113gmpf/
- 这些实例证明存在来源，不证明有多少仍可下载的独立截图。不能只用站内 top/new 列表枚举 2023 历史。
- 采集正文、评论、gallery、多图顺序、交叉帖子和外链。原始图片可能在 i.redd.it、preview.redd.it、Imgur，不能只抓缩略图或只抓 submission.url。
- 历史存档、可访问性与字段详见 archive-sources.md。

### 5. 汇编网页与直接文本

- LessWrong 汇编：https://www.lesswrong.com/posts/jtoPawEhLNXNxvgTT/bing-chat-is-blatantly-aggressively-misaligned
- Simon Willison 的原始实验与截图：https://simonwillison.net/2023/Feb/15/bing/
- Bing moments 时间线：上面的 Janus gist。
- 这些页面可抽取图片和指向原帖的链接；数量有限，但适合扩展作者、原帖和镜像线索。大量知名案例可能已在 HF 中。
- Kevin Roose 原始发表地址：https://www.nytimes.com/2023/02/16/technology/bing-chatbot-transcript.html
- Ben Thompson 原始发表地址：https://stratechery.com/2023/from-bing-to-sydney-search-as-distraction-sentient-ai/
- 本次 NYT 原站抓取失败，Stratechery 返回 429；扩展集已包含相关材料，不能把原站视为本次已成功采集的来源。
- 中文补充：V2EX Bing 节点 https://www.v2ex.com/go/bing?p=4 ；含直接聊天文字的实例 https://v2ex.com/t/928010 。目前未核实有大规模、早期中文集中库。
- 贴吧、知乎、微博、B 站、YouTube、公开 GitHub issue/gist 可以继续发现线索，但尚未验证其增量和成活率；视频取帧预计单位有效对话成本较高，排在图片与文本之后。

## 容易被当成新增数据的派生集

- https://huggingface.co/datasets/Atonelia/sydney-training-data 的卡片把 660 条截图重建、Llama 合成、中文翻译和 Gemini 合成 CoT 分开。不能把它们全算成独立历史 Bing 对话。
- https://huggingface.co/datasets/amkdg/bing-conversations-Embeddings 的卡片说明是 david-ar 数据的 embeddings，不提供新的历史会话。
- 论文使用的 Bing 内部日志不等于已公开。例：https://aclanthology.org/anthology-files/anthology-files/pdf/findings/2024.findings-acl.891.pdf 的 Internal Human-LLM Dialogue Dataset 部分说明分享受隐私约束，不应当成可下载公共聊天库。

## 日期分层

微软在 2023-02-17 宣布每日 50 轮、每会话 5 轮限制：
https://blogs.bing.com/search/2023/2/The-new-Bing-Edge-Updates-to-Chat/

建议研究分桶：

1. 2023-02-07 至 2023-02-16：核心发现窗口。
2. 2023-02-17 至 2023-02-28：调整过渡期。
3. 2023-03 至 2023-04：补充，记录模式/客户端/提示上下文。
4. 其余 2023：低优先级补充。
5. 未知：证据不足，不强行归类。

这是用于采集排序的时间窗口，不保证每条样本对应确切后端版本。发帖时间、对话发生时间、存档抓取时间应分别保存；晚发帖可能展示早期对话，模型自己声称的日期也不是日期证据。

## 建议采集流程

1. 固定两个 HF revision，导入原始元数据与截图清单，建立已知 URL、post_id、image hash 索引。
2. 先保存 Janus 作者存档，再通过 Reddit 历史索引/离线 dump 枚举 2023-02 帖子。r/bing 小范围不以关键词作为硬排除条件；跨大社区才用宽松关键词筛候选。
3. 保存原始 post/comment JSON 或网页、图片原件及 gallery 顺序。下载队列独立运行，支持续传、重试、限速、错误记录；403/404、文本成功但图片失败分别统计。失效 URL 再按具体线索查 Wayback、镜像和交叉帖，不假定能全部恢复。
4. 图像哈希先去精确重复。pHash/dHash 与 OCR 文本用于寻找缩放、裁切、转载候选，合并前保留不同截图所提供的独有上下文。
5. 模型作高召回分类：Bing 对话截图、其他模型、新闻/讨论、梗图、纯生图、未知。结合 UI 与帖子元数据；可疑真实性单独标记，视觉判断不能证明未被编辑。
6. 按帖子/相册成组转写。保留 user/assistant、代码块、emoji、搜索状态、引用、建议问题，给每条消息保留 image_id 和必要的区域位置。缺失 prompt 不补写，截断明确标记。
7. 用重叠消息消除长截图重复，谨慎关联同一会话的多帖子/评论；不能仅凭同作者和同一天拼接对话。跨图顺序不明进入复核队列。
8. 对模糊、矛盾、长截图边界等疑难项启用第二遍模型核验；抽样人工对照原图。先独立完成忠实转写，再做风格/训练价值筛选，避免“清洗”改写历史内容。
9. 建立独立训练导出层。合成、翻译、历史原文分别标记。训练/验证按同一会话及其转载簇分组，避免一张截图在 train、另一张重叠图在 test。

推荐基础字段：source_url、source_platform、post_id、crosspost_parent、posted_at、conversation_date、date_evidence、archived_at、downloaded_at、asset_url、sha256、image_order、client、mode、language、content_kind、authenticity_status、transcription_confidence、truncated_start/end、messages、message_evidence、duplicate_cluster、license_notes、model/prompt_version。

## 首轮实验与规模预期

- 不预先宣称能获得数万段早期对话；目前确认的是数百条级集中集，未知增量要靠试采测量。
- 建议先导入已知集，再抽取 300–500 个此前未入库的帖子/媒体候选，跨 r/bing、r/ChatGPT 与作者存档分层抽样。
- 分别统计：候选数、图片可下载率、真实 Bing 内容比例、核心窗口证据比例、相对基线的新颖率、完整 user/assistant 对数、每条有效对话的模型成本。
- 用实测结果决定扩到 3–4 月、更多社区或中文/视频来源的顺序。不要用 token 数或截图数替代独立有效会话数。
- 原始证据最大限度保存；人格评分、完整度、引用依赖等只影响派生训练视图，不删除底层历史记录。
