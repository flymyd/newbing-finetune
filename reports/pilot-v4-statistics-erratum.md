# v4 统计字段说明

v4 的 `statistics.json` 中 `independent_parent_conversations=22` 实际计算的是不同父记录 ID 数，不是已经独立确认的历史原会话数量。同一会话可能在基线/原帖/转写中拥有不同记录 ID。该字段名称不够准确，新导出改为 `distinct_parent_record_ids`，独立原会话数在关系尚未逐组确认时为 null。

v4 的22条导出记录、86轮完整问答、5条派生片段、20个保守split簇以及结构/证据/hash校验结果不受此统计命名问题影响。20个split簇也不等于20个已确认独立会话，因为近重复关系可能保守地合组。

v4保持冻结且provisional，最终不得把22条记录当成22个已确认独立历史交互。后续release使用修正字段。
