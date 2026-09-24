# GrowthKit — 持续成长总控

这是可跨项目复制的**外层 harness 改进控制器**，不是新模型、权重训练器，
也不是无限重试或自动付费服务。它不改 FactCircuit 的内部验证回环、历史实验
注册、默认模型、Jev 凭据或权限设置。当前实现只演化 JSON harness 描述；
将描述转换为合法任务动作，由经过审查的项目 adapter 负责。

## 已实现

每轮按“提出一个候选 → 同条件重测旧版和候选 → 校验完整结果 → 保留或拒绝”
执行。任何旧版通过而新版失败的案例都会阻止晋级；没有案例回退时，更多案例
通过才算质量改善；通过情况相同且模型调用数不增加时，较少的实测输出 token
也可成为改进。这里没有把多个指标混成可随意抬高的自评分。

状态绑定项目目录、完整配置、控制器代码和指定评测资产的 SHA-256。
`.growth/state.json` 保存当前最优 artifact、已评候选指纹、逐轮结果、调用额度
和未完成事务。拒绝候选不会覆盖最优版本，相当于保留旧版而非破坏后再回滚。
每个项目的固定 `.growth/LOCK` 防止本控制器的多个实例同时写入。
其他 agent 仍必须服从同一个项目 owner；该锁不会神奇地锁住无关工具。

调用在执行前计入 lifetime `call_budget`，失败也计入，重开进程不清零。
每次 `run` 执行 1–20 个周期，每条命令有 timeout。认证、配额、权限或协议
错误暂停到显式恢复；重复候选跳过评测；连续无进展要求新假设。
`.growth/STOP` 在命令之间检查；正在执行的命令最多到其 timeout 才结束。
异常退出遗留的 in-flight 事务不会自动重放。

## 接到当前项目或其他项目

在源仓库或解压后的目录执行：

```bash
python growthkit/install.py --project /absolute/path/to/existing-project
```

安装器复制 `growthkit/` 的四个核心文件，并在项目的
`.agents/skills/continuous-growth/SKILL.md` 与
`.claude/skills/continuous-growth/SKILL.md` 放置相同规则。
重复安装相同内容是幂等的；任何不同的既有目标文件会在预检时阻止安装。
它不覆盖既有 AGENTS.md、CLAUDE.md、模型配置、全局 skills 或凭据。
安装源文件并不表示 Codex/Claude 已发现 skill，更不表示 Jev 已完成真实调用。

## 项目接入契约

在项目根目录提供 `growth.json`，字段如下。没有默认 proposer/evaluator，
因此包本身不会悄悄启动模型或改变你的现有通道。

| 字段 | 含义 |
| --- | --- |
| `version` | 整数 1 |
| `objective` | 固定的改进目标 |
| `task_kind` | `harness`、`debug`、`research`、`code` 或 `review` |
| `case_ids` | 非空、唯一、冻结的开发评测案例 ID |
| `initial_candidate` | 非空 JSON 对象，作为当前 baseline harness artifact |
| `proposer` / `evaluator` | 两个经过审查的 argv 数组，不是 shell 字符串 |
| `frozen_files` | 项目内文件相对路径，须覆盖数据、scorer、adapter 代码及其依赖 |
| `call_budget` | 跨所有恢复轮次累计的 adapter 命令调用额度 |
| `timeout_seconds` | 每条命令的时间上限，正整数 |
| `stagnation_limit` | 连续无改进周期上限，正整数 |
| `goal_all_pass` | 是否在完整开发案例全部通过后结束，布尔值 |
| `limits` | `model_calls` 与 `output_tokens` 两个非负整数，双臂完全相同 |
| `skill_paths` | skill 名到已安装文件或目录的显式映射 |
| `required_skills` | 当前 task 路由中必须存在的 skill 名，可为空 |

必须由项目负责人/可信 adapter 确定评测资产依赖清单。控制器无法发现任意程序
所有隐含依赖，哈希检查也不能使可变的远端模型别名变成固定模型版本。

### Adapter 协议

控制器从 stdin 发送一个 JSON 对象。每个命令必须仅向 stdout 返回一个 JSON
对象；不会把原始 stdout/stderr 写进状态或直接回显。stderr 临时文件在调用后
关闭，不作为实验依据。需要额外 trace 时，由可信 adapter 在获准位置脱敏保存。

Proposer 接收 `phase=propose`、`objective`、`incumbent`、其 SHA、
`routing`、最近三条 `feedback`、`limits` 和 `pair_id`，返回：

```json
{"candidate": {"hypothesis": "one specific change", "strategy": {}}}
```

Evaluator 接收 `phase=evaluate`、`candidate`、`candidate_sha256`、
`case_ids`、`case_set_sha256`、`limits` 和 `pair_id`。返回形状为：

```json
{
  "candidate_sha256": "echo the exact request digest",
  "case_set_sha256": "echo the exact request digest",
  "outcomes": [{"id": "each requested case exactly once", "passed": true}],
  "usage": {"model_calls": 1, "output_tokens": 100}
}
```

这些是协议形状示例，不是已完成的真实评测。缺失/重复/额外案例、非布尔通过值、
负数/非整数/NaN 用量、重复 JSON key、绑定不一致或超额都会拒绝。
Adapter 也可返回 `{"status":"blocked","reason":"auth"}`；允许的 reason 为
`auth`、`quota`、`permission`、`capability`。不得静默换另一条收费通道。

### 运行、检查和恢复

```bash
python growthkit/engine.py status --project .
python growthkit/engine.py run --project . --cycles 3
python growthkit/engine.py resume --project . --reason "Verified the original authentication route is working again"
```

`resume` 只记录说明、重置暂停/停滞状态，不执行模型、不增加或清零额度。
若配置或冻结资产改变、事务不明或额度用尽，必须另建经过审查的新目标/工作区，
保留旧状态用于审计，不得手改摘要绕过校验。正常 `run` 可以持续接续同一目标；
本项目没有安装 cron、后台 daemon 或自动付费任务。

## 与已有 harness 的关系

OMX `ultragoal`/`autoresearch`、OMC、各 Ralph 实现、LoopX 都保留原本的执行环境。
同一项目只能有一个主 owner；它们不应全部套成互相重启的无限循环。
JevHarness 适用于结构化判断和任务特定策略，不能用其存在替代主模型真实可用性。
新 skill 对这些现有能力做选择和验收约束，不 vendoring、不宣称修改了上游源码。

上游参考：
- https://github.com/Yeachan-Heo/oh-my-codex
- https://github.com/TianyuCodings/JevHarness
- https://huangruiteng.github.io/loopx/docs/quota-allocation/

## 运行边界

命令必须可信。subprocess **不是安全沙箱**：代码不能阻止同权限 adapter 改文件、
产生副作用、谎报用量、暗中超额调用或读取它原本有权限访问的数据。
case 输出由独立可信 evaluator 提供，而不是 proposer 给自己评分。
model/token 上限会传给双方，且 evaluator 回执超额会被拒绝；真正的内部调用
限制仍必须由 adapter/提供商执行，`call_budget` 不是美元上限。

真实命令 runner 仅支持 POSIX（macOS/Linux），使用独立进程组并在结束/超时后
清理该组。读取的请求和回复各限制为 1 MB；临时输出文件的磁盘写入量不是硬限制。
无中断自动恢复、无自发预算充值、无自动发布、无自主生产部署。
状态文件和按内容寻址的候选用于可审计性，不是防同权限恶意篡改的安全账本。

反复用于选候选的案例是开发/选择集，不是独立测试集。
最终准确率声明需要额外冻结、事件/时间隔离、未经选择的独立评测。

## 本次验证

```bash
python -m unittest discover -s tests -p 'test_growthkit.py' -v
```

本次新增 37 个测试通过，包括真实子进程的三轮执行与跨进程接续、失败不退额度、
显式恢复、同条件双臂比较、停机、锁、数据漂移、重复 JSON key 和安装冲突。
所有任务结果均为合成控制器 fixtures；未运行真实 Jev、Codex 或 FactCircuit
新闻准确率对照。Skill 做了安装/内容一致性检查，未做独立 agent 行为 A/B 实验。
仓库完整 CI 和实际机器安装状态以 PR/本次交付报告为准，不从此处推断。
