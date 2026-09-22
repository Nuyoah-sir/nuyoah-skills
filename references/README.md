# references —— 共享前置材料

vibe-evals 系列技能开跑前要读的两份材料里，《Coding Agent 人评标准 V 2.1》全文放在这里（多轮评测技能放在仓库根目录的 `../multi-turn-eval/`）。以前这两份材料只写在本机绝对路径上，第三方机器取不到就会卡在开跑前，所以现在随仓库分发。

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `Coding Agent 人评标准 V 2.1.docx` | 原始 Word 文档，与上游发布文件逐字节一致（SHA-256 `C9B7578CA732C225A37B2BC2AC028EA37B679369D797DF66DBA83A4668482FEB`）。需要原始排版、平台截图或对外引用来源时读它。 |
| `Coding Agent 人评标准 V2.1.md` | 同一文档的完整 Markdown 全文（含 10 张平台截图，图片见 `assets/人评标准V2.1/`）。内容未删减，章节号按 Word 自动编号的实际渲染结果补齐。日常阅读与技能引用优先用这份。 |
| `assets/人评标准V2.1/` | 文档内的 10 张截图，按原位置在 Markdown 中引用。 |
| `tools/docx_to_markdown.py` | 转换脚本。上游文档更新后用它重跑，保证 Markdown 版与 docx 同步。 |

## 全文结构（对照用）

```
# Coding Agent 人评标准 V 2.1
## 一、平台操作指南          （1.1 codebuddy / 1.2 标注平台）
## 二、打分流程指南
## 三、多模型Rank            （3.1 操作流程 / 3.2 排序判断原则 / 3.3 排序理由怎么写 / 3.4 洞察怎么写）
## 四、Pointwise 多维度评分
## 五、Vibe Tags 与备注
## 六、检查
## 8. FAQ
```

其中「二、打分流程指南」「三、多模型Rank」「六、检查」在原始 docx 里是 Word 自动编号，纯文本导出工具会丢掉编号；本 Markdown 已按实际渲染结果补齐。

## 更新流程（上游文档改版时）

```powershell
# 1) 覆盖原始文档
Copy-Item -LiteralPath "<新的 Coding Agent 人评标准.docx>" -Destination ".\references\Coding Agent 人评标准 V 2.1.docx" -Force

# 2) 重跑转换（Windows 用 py 启动器；依赖 python-docx）
py .\references\tools\docx_to_markdown.py ".\references\Coding Agent 人评标准 V 2.1.docx" `
  --out ".\references\Coding Agent 人评标准 V2.1.md" `
  --assets-dir ".\references\assets\人评标准V2.1" `
  --assets-url "assets/人评标准V2.1"

# 3) 自检：所有正文与表格文字都能在 Markdown 里找到，图片 10 张齐全
```

自检口径：docx 里每个段落与表格单元格（去掉空白与 Markdown 语法后）都必须能在 Markdown 中找到；图片引用数与 `word/media/` 中的真实图片数一致。

## 使用范围

文档含公司内部平台地址（如标注平台入口）与平台截图，仓库访问范围请按同样口径控制，不要分发到授权范围之外。
