"""
demo_policy.py — 展示用基线策略（榜单占位，综合分约 50 分）

逻辑：优先从 IN 取新零件送上机床，不主动卸载已完工的件，
导致机床偶尔被堵塞。可以跑完全部订单，但周期时间偏长。
参赛者的策略应当明显优于此基线。
"""
from __future__ import annotations


def decide(obs: dict) -> dict:
    acts  = obs["legal_actions"]
    wait  = next(a for a in acts if a["kind"] == "wait")
    moves = [a for a in acts if a["kind"] != "wait"]

    if not moves:
        return wait

    # 1. 优先从 IN 取新零件（忽略机床上已完工的件，导致堵塞）
    from_in = [a for a in moves if a.get("from") == "IN"]
    if from_in:
        return from_in[0]

    # 2. 其次把零件送上机床（place）
    to_machine = [a for a in moves if (a.get("to") or "").startswith("M")]
    if to_machine:
        return to_machine[0]

    # 3. 其余任意动作
    return moves[0]
