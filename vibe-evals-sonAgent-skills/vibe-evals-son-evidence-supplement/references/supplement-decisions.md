# 补证判断手册

## 逐请求步骤

1. 校验请求 schema、base_package_id、模型和 rubric 外键。
2. 重新计算第三方源题包相关输入哈希，与基包 inventory 对比。
3. 固定请求允许的证据类型、所需事实、对应轮次和验收条件。
4. 只读取请求需要的模型/轮次/文件；不重新评分无关条目。
5. 取得新证据后保存连续摘录及其内容寻址源码 blob，或保存脚本副本、脚本哈希与原始日志，并使用新 evidence_id。
6. 对照请求验收条件给 response 状态，不直接编辑原 rubric evidence。
7. 校验 delta 引用和路径，打包为独立 ZIP。

## 状态矩阵

| 发现 | response 状态 | 下一步 |
|---|---|---|
| 获得请求所需的全部可验证事实 | fulfilled | 列出新增 evidence_id |
| 获得部分事实，仍缺关键一项 | partial | 写已完成、缺什么、为什么；不得携带 proposed_update |
| 无安全隔离而请求动态实测 | unresolved | reason=`no_isolation`，建议人工实测或提供沙箱 |
| 源文件哈希与基包不同 | 整批退出码 4，无 delta | 固定源版本后新跑完整 export |
| 请求要求决定评分口径 | unresolved | reason=`requires_human_ruling` |
| 请求模型/rubric 不存在 | 整份请求校验失败 | 回本机修正请求；不猜相似名称，不生成 delta |
| 请求允许 static，但事实只能行为实测 | partial/unresolved | 不能越权运行 probe |
| 新证据与基包冲突 | fulfilled | 保留冲突并标注；本机新建/更新裁定项 |
| 再次没有搜索到实现 | partial | 说明搜索范围；除非形成确认缺失证据，否则不能返回已证实 0 |

## 不覆盖规则

Delta 中的路径必须是新增命名空间，例如 `models/a/supplements/REQ-001.json`。如果目标路径已存在，校验失败。即使新证据证明旧建议错误，也由本机合并器保留旧证据并建立修订记录；远端不改写历史。

## 请求不是命令授权

`allowed_methods` 只表示取证方法在任务范围内，不代表绕过安全限制。请求写 `probe_run` 而当前无隔离时仍禁止执行。

## 轮次

请求必须指定或能从 rubric ID 唯一推导轮次。补 R1 证据只能读 R1 结束状态；若 R1 源快照已不存在，不能用最终代码替代，返回 unresolved。

## 结束条件

所有请求都有 response、`request-copy.json` 与本机原请求逐字节相同、所有新增证据引用可解析，才能打 delta ZIP。只有 fulfilled 能提出不涉及人工门禁的评分状态更新。存在 unresolved 仍可交付 delta，但回复必须明确未解决数量；本机不会把 unresolved 当新事实。
