# News Tracing Agent

基于 LLM 的新闻溯源 Agent，输入一条新闻或话题，**直接回答**用户关心的问题，并附带透明的多维度可信度指标。同时生成事件时间线、因果分析、多源事实核查等详情供展开查阅。

## API 工作流架构

```
Phase 1: 解构 + 搜索规划
    ↓
Phase 2: 双轨并行
    Track A: 多角度信源采集 → 合并去重 → 交叉验证
    Track B: 递归因果挖掘（最多 3 层）
    ↓
Phase 3: 时间线构建 + 综合研判
    锚定验证 → 事件时间线 → 多方视角 → 可信度指标 → 直答生成 → 详细研判
```

## 快速开始（本地 Codex 路由）

```bash
# 从仓库根目录进入集成目录
cd integrations/news-tracing-master
python main.py "美伊冲突" --model gpt-6-astra --reasoning-effort low
```

本地默认委托仓库根目录的 FactCircuit `trace-news`，使用 Codex 登录和本地 CLI；不需要 API key 或额外 provider。默认模型为 `gpt-6-astra`，也可以用 `--model` 显式选择当前本地可用的其他模型。设置 `FACTCIRCUIT_MODEL` 可提供项目级默认模型。

“本地”指启动和登录通道；Astra 推理仍在云端完成。

本地路径不会自动读取 `.env`；请先导出项目默认值，或在命令前设置一次：

```bash
export FACTCIRCUIT_MODEL=gpt-6-astra
FACTCIRCUIT_MODEL=gpt-5.6-luna python main.py "美伊冲突" --reasoning-effort low
```

## 输入与输出

```bash
python main.py "美伊冲突" --output RESULT.json
python main.py --input CASE.json --output RESULT.json --model gpt-6-astra
```

`--input` 接收 JSON 新闻输入，`--output` 写入 JSON 结果；也可直接提供位置参数新闻文本。

原有的富 API 工作流需要显式选择 API 路由。该路径需要安装依赖并自行配置 provider：

```bash
pip install -r requirements.txt
cp .env.example .env
```

```
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=provider-model-id
OPENAI_SEARCH_MODEL=provider-search-model-id
```

```bash
python main.py "美伊冲突" --tunnel api
```

使用 `--tunnel api` 才启用该 provider 路径；API 与本地路由不会自动回退。`OPENAI_MODEL` 必须是 provider 实际支持的模型 ID，不能假定 API 提供 Astra。

## 使用

```bash
# 直接传入话题
python main.py "美伊冲突"

# 指定因果挖掘深度
python main.py "美伊冲突" --depth 2

# 交互模式（不传参数）
python main.py
```

## 报告输出

本地路径输出 FactCircuit JSON，包括来源链、事实判断和调用记录。显式 API 路径使用原有 Rich 报告，按以下顺序输出：

| 模块 | 说明 |
|------|------|
| **直答** | 一段话回答用户问题：核查输入声明、纠偏、事件真相、因果主线、争议标注、可信度锚点 |
| 可信度指标 | 透明原始数据：N 个信源 / N 类媒体 / 一致数 / 争议数 / 因果锚定率 |
| 事件时间线 | 按时间排序的关键事件，标注显著性和多方视角 |
| 因果事件树 | 递归挖掘的因果链，每个节点标注 [有据] 或 [推测] |
| 事实核查 | 多源一致的事实 vs 存在分歧的事实 |
| 信源一览 | 所有采集到的信源及其类型 |
| 关键发现 / 信息缺口 / 立场标注 | 综合研判结果 |

## 项目结构

```
news/
├── main.py                 # 默认本地分发；API 需显式选择
├── legacy_main.py          # 原 API 工作流和 Rich 报告渲染
├── agent/
│   ├── core.py             # 双轨并行编排 + 时间线构建
│   ├── llm_client.py       # legacy API client（支持搜索模型）
│   ├── models.py           # 数据模型（CredibilityBreakdown, TimelineEvent, ...）
│   └── prompts.py          # 各阶段 prompt 模板
├── requirements.txt
├── .env.example
└── .gitignore
```

本地默认入口由 `main.py` 委托仓库根目录的 FactCircuit；上图中的 Rich renderer 与
`agent/` workflow 属于显式 API legacy 路径。
