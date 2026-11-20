# 参赛包使用说明

> 单臂柔性机加单元在线调度 · 黑客松主赛题

---

## 目录结构

```
hackerson/
├── problem/
│   └── 赛题说明.md          ← 完整赛题（从这里开始读）
├── simkit/
│   ├── engine.py            ← 仿真引擎（与正式评测器等价）
│   ├── scenarios.py         ← 9 个开发实例定义（F2×3 / F3×3 / F6×3）
│   ├── evaluate_policy.py   ← 批量评测入口（主要用这个）
│   ├── runner.py            ← stdio 协议运行器（与正式协议一致）
│   ├── visualize.py         ← 甘特图 + 订单流程可视化
│   └── baselines/
│       ├── simple.py        ← 三条参考基线（worst / fifo / greedy / batch）
│       └── __init__.py
└── README.md                ← 本文件
```

---

## 快速开始（3 步）

### 第 1 步：确认环境

需要 Python 3.10+，无额外依赖。

```bash
python3 --version
```

### 第 2 步：跑一次评测，确认环境正常

```bash
cd hackerson
python3 -I simkit/evaluate_policy.py \
    --policy simkit.baselines.simple \
    --fn baseline_greedy \
    --label greedy
```

正常输出示例：

```
instance               family   greedy得分    team_ms    base_ms   ref_ms  done
dev-F2-101             F2         66.08      296500     417500   234400  2/2
...
  组合分(0.7+0.3):   64.12
```

### 第 3 步：写你的策略，替换 baseline_greedy

```python
# my_policy.py
def decide(obs: dict) -> dict:
    """
    obs["legal_actions"] 是当前合法动作列表，每个元素形如：
      {"action_id": 0, "kind": "move", "part": "P003",
       "from": "BUF", "to": "M1", "nominal_ms": 28000, "setup_ms": 40000, ...}
      {"action_id": N, "kind": "wait"}
    返回其中一个字典（含 action_id 即可）。
    """
    acts = obs["legal_actions"]
    # 示例：直接返回第一个动作
    return acts[0]
```

评测你的策略：

```bash
python3 -I simkit/evaluate_policy.py \
    --policy my_policy.py \
    --fn decide \
    --label my_v1
```

---

## 评测命令详解

```bash
python3 -I simkit/evaluate_policy.py \
    --policy <模块路径或 .py 文件>   # 必填
    --fn     <函数名>               # 默认 decide
    --label  <显示标签>             # 任意字符串，用于区分版本
```

**输出含义：**

| 列 | 含义 |
|---|---|
| `team_ms` | 你的策略的 mean_flow_ms |
| `base_ms` | baseline_worst 的 mean_flow_ms（0 分锚点） |
| `ref_ms` | min(simple baselines) × 0.80（100 分锚点） |
| `done` | 完成订单 / 总订单 |
| 族均分 | 该族 3 个实例的平均得分 |
| 组合分 | 0.7 × 全族均分均值 + 0.3 × 最差两族均分均值 |

**参考分数区间：**

| 策略水平 | 组合分范围 |
|---|---|
| 随机 / 有 bug | < 0 |
| 直接问 AI，一次性产出 | 20 – 45 |
| 简单规则基线（greedy / batch） | 55 – 70 |
| AI + 评测 harness 自动迭代 | 65 – 90 |

---

## 可视化

```bash
python3 -I simkit/visualize.py \
    --instance dev-F6-301 \
    --policy   example_policy.py \
    --fn       decide \
    --out      result.html
```

想看和 OJ 网站上一样的 3D 策略动画，用 `visualize_3d.py`（需联网加载 Three.js）：

```bash
python3 -I simkit/visualize_3d.py \
    --instance dev-F6-301 \
    --policy   example_policy.py \
    --out      result_3d.html
```

用浏览器打开 `result.html`，可以看到：

- **资源甘特图**：每台机床和机械臂的加工 / 换型 / 停机 / 搬运区间（鼠标悬停显示详情）
- **订单流程条**：每个订单从到达到完成的时间跨度
- **指标卡**：mean_flow、makespan、机械臂利用率、换型次数、停机次数

---

## stdio 协议运行器

正式评测使用标准 I/O 协议。你可以用 runner 验证协议对接是否正确：

```bash
# 内联模式（快，适合开发）
python3 -I simkit/runner.py \
    --instance dev-F3-201 \
    --policy   my_policy.py \
    --fn       decide

# stdio 模式（与正式评测协议一致）
python3 -I simkit/runner.py --instance dev-F2-101 | python3 my_stdio_policy.py
```

stdio 策略的收发格式见 `problem/赛题说明.md` 第 7 节。

---

## 在策略里使用仿真引擎做 rollout

仿真引擎是确定性的，单局耗时 < 1 ms（开发集实例），可以在策略里调用：

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from simkit.engine import Engine

def decide(obs: dict) -> dict:
    # 获取当前实例（通过保存 init 消息里的场景）
    # 对每个候选动作跑完整仿真，选 mean_flow_ms 最小的
    ...
```

参赛包不附带 rollout 示例，这部分留给你自己实现。

---

## 开发实例

| 实例 ID | 族 | 工件数 | 特点 |
|---|---|---|---|
| dev-F2-101/102/103 | F2 单机瓶颈 | 8 | B 型工件多，M1 是 op0 唯一快速机床 |
| dev-F3-201/202/203 | F3 高换型 | 8 | 换型时间（35–40 s）接近加工时间 |
| dev-F6-301/302/303 | F6 扰动 | 9 | 1 次停机 120–200 s，需切换备用路线 |

正式评测包含 2 个隐藏族（H1 / H2），参数在公开范围内但机制组合未在开发集出现。策略不能只针对已知族调优。

---

## 进化日志格式

提交时须附 `evolution/lineage.jsonl`，每行记录一个版本：

```json
{
  "id": "v03",
  "parent": "v02",
  "change": "卸载高换型工件时先入 BUF，避免 F3 换型浪费",
  "eval": {"F2": 76.9, "F3": 57.6, "F6": 60.2, "combined": 63.6},
  "kept": true,
  "insight": "F3 的核心损失不是路线选择，而是换型触发时机"
}
```

这份日志同时是你策略演化过程的证明，也是评委审查的依据。

---

## 常见问题

**Q：策略崩溃了怎么判？**  
本局无效，计 −100 分。

**Q：可以在 `decide` 里调用 `Engine` 做 rollout 吗？**  
可以，但算入 1 秒单次决策和 20 秒累计限额。开发集实例单局 < 1 ms，合理使用。

**Q：可以在初始化时预处理（比如解析工艺表、预计算路线）吗？**  
可以，`init_cpu_ms` 有 2 秒的初始化额度，单独计算，不占决策额度。

**Q：`setup_ms` 字段为空 `{}` 表示什么？**  
该实例没有换型约束（F2 / F6 场景族），所有工件类型切换代价为 0。

**Q：`machine_setup_from` 为 `null` 表示什么？**  
机床从未加工过工件（冷机），此时目标工件无需换型直接开始加工。

**Q：正式评测会告诉我是哪个族吗？**  
不会。策略需要从 `init` 消息的参数（是否有 `setup_ms`、是否有 `disruptions`、工件构成）自行判断，或设计对所有族鲁棒的通用策略。
