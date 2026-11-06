# P3 试点候选清单

时间 2026-11-05T19:29:32.708388+00:00。从已分类的新候选中按来源配额、核心期优先及实际不足重新分配，选择 500/500 个候选组。分母是独立候选组，非图片或最终会话。

```json
{
  "created_at": "2026-11-05T19:29:32.708388+00:00",
  "target": 500,
  "selected_groups": 500,
  "available_classified_novel_groups": 987,
  "by_layer": {
    "chatgpt": 68,
    "author_archive_and_web": 100,
    "bing": 297,
    "freesydney_and_singularity": 32,
    "bingchat": 3
  },
  "by_priority": {
    "0": 441,
    "1": 59
  },
  "quota_shortfalls_reallocated": {
    "bingchat": 45,
    "freesydney_and_singularity": 16
  },
  "config_hash": "490f222e63f4d8ee75156c6d730851430d12a7a9bca86b2a7b607332d49450ae",
  "cohort_created_at": "2026-11-05T11:04:05.325721+00:00",
  "cohort_hash": "f89f2146b687d97c7392e394cb3cf9ebd34150d2311875bb29b0dc093a483faa",
  "limitation": "This establishes candidate-level novelty and evidence availability only. Transcription, conversation-level deduplication and ShareGPT acceptance are separate gates."
}
```

逐组来源/原文哈希/处理状态见 P3-pilot-manifest.json；新颖性排除已知基线原帖及全图完全重复，但还不证明会话内容不重复。剩余模型处理和质量验收必须继续。
