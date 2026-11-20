"""
example_policy.py — 参赛选手起点示例策略

这是一个可以直接运行的策略框架，演示了如何读取观测、
分析合法动作，并做出决策。得分约 30–45 分（开发集）。

运行评测：
    python3 -I simkit/evaluate_policy.py --policy example_policy.py --fn decide --label example
"""
from __future__ import annotations


def decide(obs: dict) -> dict:
    """
    obs 包含：
        t_ms          : 当前仿真时刻（毫秒）
        robot         : {"pos": "M1", "holding": null, "state": "IDLE"}
        machines      : [{"id":"M1","state":"IDLE","part":null,"setup_from":"B",...}, ...]
        buffer        : ["P003"]              # 缓冲区中的工件 ID 列表
        parts         : {"P001": {"type":"B","next_op":1,"location":"M2","on_machine_done":true}, ...}
        orders        : {"O01": {"release_ms":0,"done_parts":1,"total_parts":4}, ...}
        legal_actions : [{"action_id":0,"kind":"move",...}, ..., {"action_id":N,"kind":"wait"}]

    返回 legal_actions 中的某一个动作（含 action_id 即可）。
    """
    acts  = obs["legal_actions"]
    parts = obs["parts"]

    wait  = next(a for a in acts if a["kind"] == "wait")
    moves = [a for a in acts if a["kind"] == "move"]

    if not moves:
        return wait

    # ── 策略逻辑 ─────────────────────────────────────────

    # 1. 优先把已完成加工的工件卸下（释放机床，避免堵塞）
    unload = [a for a in moves if parts.get(a["part"], {}).get("on_machine_done")]
    if unload:
        # 优先送 OUT（全部工序完成的工件），其次送 BUF
        to_out = [a for a in unload if a["to"] == "OUT"]
        if to_out:
            return to_out[0]
        return unload[0]

    # 2. 把完成所有工序的工件送 OUT
    to_out = [a for a in moves if a["to"] == "OUT"]
    if to_out:
        return to_out[0]

    # 3. 把工件送往机床加工：优先选换型代价小 + 加工时间短的组合
    to_machine = [a for a in moves if a["to"] not in ("OUT", "BUF")]
    if to_machine:
        # 按 setup_ms + nominal_ms 升序，选代价最小的
        return min(to_machine,
                   key=lambda a: a.get("setup_ms", 0) + a.get("nominal_ms", 0))

    # 4. 没有其他选项时等待
    return wait


# ── 以下是帮助你理解观测的工具函数，不参与评分 ──────────────

def summarize_obs(obs: dict) -> str:
    """把当前观测格式化成一行摘要，便于调试。"""
    t = obs["t_ms"]
    machines = {m["id"]: m["state"] for m in obs["machines"]}
    buf_count = len(obs["buffer"])
    n_acts = len(obs["legal_actions"]) - 1  # 减去 wait
    return (f"t={t//1000}s  machines={machines}  buf={buf_count}  "
            f"legal_moves={n_acts}")


if __name__ == "__main__":
    # 直接运行本文件可以看一局的运行轨迹（用 greedy 场景测试）
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from simkit.engine import Engine
    from simkit.scenarios import INSTANCE_BY_ID

    inst_id = sys.argv[1] if len(sys.argv) > 1 else "dev-F2-101"
    sc = INSTANCE_BY_ID[inst_id]
    eng = Engine(sc)
    gen = eng.run()
    step = 0
    try:
        obs = next(gen)
        while True:
            print(f"[step {step:3d}] {summarize_obs(obs)}")
            action = decide(obs)
            chosen = next(a for a in obs["legal_actions"]
                          if a["action_id"] == action["action_id"])
            print(f"           → {chosen}")
            obs = gen.send(action)
            step += 1
    except StopIteration as e:
        r = e.value
        print(f"\n完成！mean_flow={r.get('mean_flow_ms')}ms  "
              f"done={r.get('orders_done')}/{r.get('orders_total')}")
