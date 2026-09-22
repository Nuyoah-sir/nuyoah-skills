# 模型 {{MODEL_NAME}} 评测结果

> 测试时间：{{TEST_TIME}} | 执行单元：multi-turn-eval Phase 2+4

---

## 编译状态

**结果**：{{COMPILE_STATUS}}

{{COMPILE_ERRORS}}

## 黑盒测试

**结果**：{{PASS_COUNT}} 通过 / {{FAIL_COUNT}} 失败 / {{TOTAL_COUNT}} 总计（通过率 {{PASS_RATE}}%）

### 测试明细

| 测试项 | 结果 | 说明 |
|--------|------|------|
{{TEST_DETAIL_TABLE}}

### 失败明细

{{FAILURE_DETAILS}}

## 逐轮分析

> **注意**：每轮的 `Session ID` 必须从会话记录或原始对话中提取；无法获取时如实标注原因，禁止保留 `[待填写]`。模型级 `traceId` 同样必须来自真实导出数据，不得猜测或使用占位符。

{{ROUND_ANALYSIS}}

## 五维度打分

| 维度 | 分数 (0-4) | 说明 |
|------|-----------|------|
| 指令与约束遵循 | {{SCORE_COMPLIANCE}} | {{NOTE_COMPLIANCE}} |
| 功能交付完整性 | {{SCORE_DELIVERY}} | {{NOTE_DELIVERY}} |
| 任务完成效率 | {{SCORE_EFFICIENCY}} | {{NOTE_EFFICIENCY}} |
| 架构合理性 | {{SCORE_ARCHITECTURE}} | {{NOTE_ARCHITECTURE}} |
| 上下文理解 | {{SCORE_CONTEXT}} | {{NOTE_CONTEXT}} |
