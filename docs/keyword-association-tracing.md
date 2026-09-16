# 原文关键词、二次关联与逐条溯源

`trace-keywords` 是新的判定入口：直接读取完整原文，提取有精确位置的关键词，再用一次独立调用生成待核查关系，最后逐条寻找原始证据。不先生成 summary，不把先前的模型结论当作后续证据。原有 `trace-phrases` 保留为逐个字面片段的冻结比较入口。

## 运行

创建 `input.json`：

```json
{
  "url": "https://www.axios.com/2022/10/11/nasa-dart-asteroid-deflection",
  "limits": {
    "max_keywords": 24,
    "max_associations": 12,
    "max_calls": 5,
    "max_documents": 6,
    "max_searches": 2,
    "max_chars": 200000,
    "context_chars": 160
  }
}
```

使用现有本地 Codex 登录运行：

```sh
python -m factcircuit trace-keywords input.json --output private-data/keyword-run-01.json
```

可指定 `--model`、`--reasoning-effort` 和每次模型调用的 `--timeout`（秒）。默认模型为 `FACTCIRCUIT_MODEL` 或 `gpt-6-astra`，默认推理强度为 `low`，默认超时为 180 秒。本地登录仍可调用远端模型，不是离线推理。此命令没有 `--arm`。

输入只接受 `url` 和可选的 `selectors`、`as_of`、`limits`。`selectors` 是核查重点，例如 `["32 minutes", "73 seconds"]`，也可使用已核对的 `{"start": 120, "end": 122}` 位置；省略时从完整原文提取。选择重点不证明已覆盖全文。`as_of` 必须是带时区的 ISO 时间戳。

`--output` 必须是新文件；不会覆盖原输入或已有尝试。报告包含全文、证据及模型输入输出，应保留在本地。正常完成返回退出码 0；部分完成或执行失败返回 1，并保留已有报告；输入、配置或输出写入错误返回 2。

## 从原句提取，不经过摘要

第一步只提取原文中的字面片段，包括主体、对象、动作、数字与单位、时间、否定词、限定条件和消息归属。例如“拟收购”“已完成”“据某人称”“仅在实验条件下”不能在提取时被改写或删去。

每个关键词保留原文、所选的第几次出现、`start` / `end`、来源文本哈希和前后文。位置使用存储文本中的 **零起点 Unicode 码点，右端不包含**；它们不是 HTML 字节、UTF-16 位置或屏幕坐标。HTML 解析会解码实体并插入结构换行；存储后不再静默正规化文本。重复出现的同一短语必须指向具体的一次出现。程序核对字面位置，模型负责解释语义，两者不能混为一谈。

第二步独立生成关系，用已经验证的关键词 ID 填写：

`主体—动作—对象—时间—数值—条件—消息来源`

缺失项保留为空，不用常识补全事实。关系带有原句锚点，并区分原文明示的 `explicit` 与模型推测、仍待核查的 `inferred`。例如“公司 A 拟收购公司 B”不能被直接转成“公司 A 已完成收购公司 B”。推测可以作为检索假设，但其核查结果不能计入文章事实得到支持的数量。

## 按关系组合关键词回到原始材料

每次搜索必须包含该关系中至少两个不同、匹配位置不重叠的关键词文本，查询最多 512 字符；同一个“公司 A 集团”中的“公司 A”和“公司 A 集团”不能重复计数。核查策略要求同时寻找支持和相反方向的原始材料，例如：

- 公司 A + 公司 B + 收购完成。
- 公司 A + 公司 B + 收购终止。

检索应回到公告、原始研究、完整采访、勘误及后续更新。也可以打开已经读取材料中的实际链接。搜索标题和摘要只用于发现网页，不能作为证据。调用次数或搜索预算不足、网页打不开、证据只覆盖部分限定条件时，应保留证据缺口，不得为了给出结论而补造材料。

报告保留实际请求与打开记录、完整证据原文、URL、可用时间及其依据、抓取时间、原始响应和文本哈希，以及精确引文位置。日期只代表相应记录的时间含义：当前抓取到一篇旧文章，不证明当前版本在历史截止日前已经存在。需要历史核查时，必须使用有可核实存档时间的采集器。

转载、同一上游来源的转述、同一发布方的自述，不因为不同网址而成为多个独立证据。来源独立性和证据语义仍由模型判断，程序的精确引文校验只证明文本确实存在。

## 先逐条核查，再检查组合关系

先核查独立的原子事实，再单独核查因果、时间顺序、引用归属及其他组合关系。组合核查仍读取原始材料，不把前一条模型判断当作证据。“A 发生”“B 发生”都得到支持，也不自动证明“A 导致 B”；人物说过一句话，也不自动证明这句话描述的事件为真。

面向读者使用四类结果，同时保留机器状态的差异：

| 显示结果 | 机器状态 | 含义 |
|---|---|---|
| 证据支持 | `supported` | 对该条、该范围的关系有对应证据支持 |
| 证据反驳 | `contradicted` | 对该条关系存在对应反证 |
| 证据不足 | `unresolved` | 证据缺失或不足以作出判断 |
| 存在歧义 | `ambiguous` / `conflicting` | 前者为含义或适用范围不明确，后者为同一关系有相互冲突的证据；报告保留两者区别 |

汇总另外保留 `attribution_only` 和 `hypothesis_only`：前者只说明来源如何表达，后者针对模型推测的假设；两者都不等同于文章中的经验事实得到认证。

`mixed` 表示**不同的经验事实主张中，至少一条得到支持，同时至少另一条遭到反驳**。它不要求真假各占一半，也不把“证据不足”当作假。“同一关系存在冲突”与“不同关系有真有假”分开报告。不能采用多数票；无论多少背景细节得到支持，都不能据此认证整篇文章或其因果结论。所有结论只覆盖实际提取并完成核查的关系。

报告中的 `keywords` 保存字面锚点；`associations` 保存 `assertion_origin`、七个关键词 ID 数组组成的 `slots` 和原文范围；`items` 保存逐条核查结果、中文 `label`、引文和取证记录。`atomic_checks` / `composition_checks` 分别列出两阶段的条目 ID；`assessment` 保存各结果分桶，`combination_assessment` 单独保留组合核查的结果。未选出组合关系时，后者的 `check_status` 是 `no_composition_relations_selected`，不是组合成立。`coverage` 明示 `selected_associations_only`，`whole_document_verified` 始终为 `false`。模型仍可能漏提主张，或错误判断一条关联是否为原文明示。

## 预算与边界

`max_keywords` 默认 24、最多 100；`max_associations` 默认 12、最多 100。其余预算按每条关系分别应用：

| 字段 | 默认值 | 允许范围 | 用途 |
|---|---:|---:|---|
| `max_calls` | 5 | 1–10 | 包含最终判断的模型调用上限 |
| `max_documents` | 6 | 1–20 | 文档数上限，包含原文 |
| `max_searches` | 2 | 0–5 | 公共搜索上限 |
| `max_chars` | 200000 | 1–1000000 | 已接纳证据的总文本容量 |
| `context_chars` | 160 | 0–5000 | 每个锚点前后文的窗口大小 |

完整原文始终保留；邻近窗口只是定位工具。每条关系使用独立的检索预算，不复用其他关系的模型意见。

准备阶段最多调用两次模型：一次关键词提取，一次二次关联。后续模型调用上限为 `max_associations × max_calls`，所以完整运行上限为 `2 + max_associations × max_calls`；提前停止时实际调用可能更少。默认配置和上述示例的上限都是 62 次。预算用尽和无有效关联都必须看报告中的实际状态，不能按“调用已结束”解释成事实得到支持。

公开网页解析不保证抓到了完整视觉页面；图片、PDF、仅靠 JavaScript 展示的内容和表格视觉关系可能需要额外核查。精确匹配不证明语义正确，找到原始来源也不证明来源自身为真。此实现和模拟测试不构成真实世界准确率、提前识别造假能力或“已确保真假”的实验结论。

## LightRAG 与 DEFAME 的参考范围

这两项是架构参考，不是本命令的运行时依赖；实现仍在本项目中，也不会把外部框架的摘要直接当作事实证据。

- **LightRAG**：参考其[实体与明确关系提取](https://github.com/HKUDS/LightRAG/blob/044eac0b5040191fe73a99e2847ba63b30338763/lightrag/prompt.py#L51)、[双层关键词](https://github.com/HKUDS/LightRAG/blob/044eac0b5040191fe73a99e2847ba63b30338763/lightrag/prompt.py#L432)以及[实体和关系检索](https://github.com/HKUDS/LightRAG/blob/044eac0b5040191fe73a99e2847ba63b30338763/lightrag/operate.py#L4857)的思路。本命令额外要求原文位置和关联槽位绑定；不采用其[实体与关系描述摘要](https://github.com/HKUDS/LightRAG/blob/044eac0b5040191fe73a99e2847ba63b30338763/lightrag/operate.py#L345)作为证据。
- **DEFAME**：参考其[主张提取模块](https://github.com/multimodal-ai-lab/DEFAME/blob/0d5c2eb5e07cfa8a673351e765c9c576070cdd6c/defame/modules/claim_extractor.py#L28)和[逐条主张核查](https://github.com/multimodal-ai-lab/DEFAME/blob/0d5c2eb5e07cfa8a673351e765c9c576070cdd6c/defame/fact_checker.py#L105)。本命令保留每条证据分桶和真假混合情况，不沿用其[整篇预测汇总](https://github.com/multimodal-ai-lab/DEFAME/blob/0d5c2eb5e07cfa8a673351e765c9c576070cdd6c/defame/fact_checker.py#L172)，也不把 summary-based 流程中的中间推理当作原始证据。

以上链接固定到所查阅的提交，便于核对参考边界；不能据此推断本项目继承了这两个项目的评测结果。
