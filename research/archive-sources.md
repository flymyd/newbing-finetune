# 2023 New Bing：Reddit 历史存档核查

本文是来源可用性研究快照；最终训练成品采用 [ShareGPT 纯文本多轮输出规范](../docs/SHAREGPT_EXPORT.md)。截图、正文与评论是采集载体，是否满足多轮训练条件由后续重建/导出阶段判断。

核查日期：2026-11-05。这里只讨论采集方案，未下载大型 archive，未运行爬虫。

## 核心结论

- **先取历史帖子索引，再取截图文件。** Arctic Shift/Pushshift 的 JSON dump 是 Reddit 帖子与评论记录，不等于保存了截图二进制；必须单独验证 `i.redd.it`、`preview.redd.it`、Imgur 等媒体 URL。
- **不能只采 r/BINGChat。** 已验证 r/bing、r/ChatGPT 和 r/freesydney 的 2023 原帖；其中 r/freesydney 有直接粘贴的长篇对话，截图限定会漏掉这类数据。
- **Arctic Shift 是值得优先试验的历史索引来源，但当前环境 API 返回 403。** 其仓库及下载界面可访问，尚不能把文档上“支持”当作本机已经成功采集。
- **Pushshift 在线 API 不适合默认主路径。** Reddit 官方申请说明将它限制为经批准的 moderator 的 moderation 用途。
- **Internet Archive 只能作为待核实的补救路径。** 实测 March 2023 Pushshift item 的 metadata 返回 `is_dark: true`；ArchiveTeam 自己也说明 Reddit WARC 下载受到限制。

## 已核实来源

### Arctic Shift

一手项目入口：[GitHub](https://github.com/ArthurHeitmann/arctic_shift)。

- [下载工具](https://arctic-shift.photon-reddit.com/download-tool)：界面提供 subreddit/user、起止日期、posts/comments 导出。
- [API 文档](https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md)：提供 posts/comments 的日期查询、ID 批量查找、评论树和聚合接口；不保证可用性。
- [历史 dump 索引](https://github.com/ArthurHeitmann/arctic_shift/blob/master/download_links.md)：列有包含 2023 的全集，以及 `2005-06 - 2023-12 top 40k subreddits` torrent。后者可望按版块选择文件，实际文件名单与可下载性仍需验证。
- [数据说明](https://github.com/ArthurHeitmann/arctic_shift/blob/master/file_content_explanations.md)：每条 JSON 对应一篇帖子或一条评论，2023-04 起的格式规则与此前 Pushshift dump 有差异。
- [帖子 schema](https://github.com/ArthurHeitmann/arctic_shift/blob/master/schemas/RS.ts) 包括 `gallery_data`、`media_metadata`、`preview`、`url_overridden_by_dest`、crosspost 相关字段。原始 JSON 应尽量完整保留，避免仅导出 `url` 而丢掉多图相册顺序及备选媒体地址。

本环境实测（2026-11-05）：`/api/posts/search` 查询 bing/BINGChat/freesydney 的 2023-02 数据、`/api/posts/ids?ids=110pbgm` 均返回 HTTP 403；聚合查询也返回 403。Academic Torrents 的 top-40k 详情页在 web 工具返回 404，`.torrent` 请求返回 403。**这不能证明存档不存在，但当前不能给出成功采集量或子版块精确数量。**

### Reddit 原帖：比 r/BINGChat 更广的种子池

- [r/BINGChat](https://www.reddit.com/r/BINGChat/)：当前社区页可访问，页面有图片和纯文本内容，但未验证其 2023 全量规模。
- [r/bing：2023-02-17 原帖](https://www.reddit.com/r/bing/comments/114tv6k/)：证明该版块当时有大量 Sydney 讨论；该例本身是 meme，正好说明必须识别“对话截图 / meme / 评论讨论”。
- [r/ChatGPT：2023-02-12 Bing 聊天截图帖](https://www.reddit.com/r/ChatGPT/comments/110pbgm/)：页面正文标有五张截图，值得纳入跨版块检索。当前网页正文提取没有给出所有图片 URL，不能把“帖子尚在”当作“截图完整可下载”。
- [r/freesydney：2023-08-05 长对话帖](https://www.reddit.com/r/freesydney/comments/15j8w1z/)：正文直接保存 A/S 轮流对话，另指向前一场对话，且说结尾在评论区。这说明**要收正文、评论和出站链接**，并要保留原作者角色标注中的疑似错误、缺省段落等不确定性。

这些是已确认有内容的种子，并不构成版块规模排名；还未完成按日期计数及媒体存活率抽样。

### Pushshift

[Reddit 官方 Pushshift Access Request](https://mods.reddithelp.com/hc/en-us/articles/16678106585741-Pushshift-Access-Request) 明确要求批准，限 moderator 的 moderation use cases。历史开源 API 文档仍能搜到，不代表现行在线端点向一般研究项目开放。

### ArchiveTeam / Internet Archive

[ArchiveTeam 项目说明](https://wiki.archiveteam.org/index.php/Reddit) 将 textual archive 与网页 WARC 区分，说明其文本归档不含 Reddit 托管图片视频；它也记录了 Reddit WARC 下载限制。因此不要把“2023 网页存档存在”理解成“可以批量获取全部媒体”。

直接实测：

- [March 2023 item](https://archive.org/details/pushshift-reddit-2023-03) 的 [metadata](https://archive.org/metadata/pushshift-reddit-2023-03) 返回 `{"is_dark": true}`。
- `pushshift-reddit-2023-02` metadata 未返回文件清单，不能列为已验证可用下载源。

## 建议试点

1. 范围先设为 2023-02-07 至 2023-04-30，同时保留全年扩展能力；对早期 Sydney 单独标版本/时期。
2. 历史索引先试 r/bing、r/BINGChat、r/freesydney；r/ChatGPT 以 Bing/Sydney 等词做补充召回。离线 dump 和在线接口互为适配器，别把工程绑死在单一 API。
3. 从每个来源抽样后统计：帖子数、含媒体比例、图片链接成功率、确认为 Bing 的比例、完整对话数、去重后轮次数。未做这些前不承诺“几万条对话”。
4. 先保存 raw JSON/正文与媒体 manifest（原始 URL、post/comment ID、日期、相册顺序、获取状态、hash），再下载原图并进行视觉识别；失败记录单独保存以便重试或人工补源。
5. 文本、图片和跨帖相同对话分别去重。用视觉模型判“是 Bing 截图”不能验证截图真实性；来源证据、时间和对话完整性应独立记录。

## 尚未验证

- 各子版块 2023 的精确量与全量覆盖率。
- 三年前截图在各 CDN 的存活率；不能凭少量网页成功推断。
- top-40k torrent 是否包含 BINGChat/freesydney，以及当前 seed 状态。
- Wayback/CDX 对具体媒体 URL 的成功恢复率。
- 每项数据的训练许可；公开可访问与可直接再分发、训练不是同一个属性，应另设 rights 字段。
