"""
基线策略

baseline_fifo   : 先到先服务（参考基线，定义 0 分）
baseline_greedy : 优先选总耗时（setup + nominal）最短的 move
baseline_batch  : 优先选换型代价为 0 的 move（批量同类）；其次最短总耗时
"""
from __future__ import annotations


def _priority_move(acts: list[dict], parts: dict,
                   prefer_no_setup: bool = False,
                   prefer_fast: bool = False) -> dict | None:
    """
    在 move 动作里按优先级选择。
    1. 先把 on_machine_done 的工件搬走（释放机床）。
    2. 把完成品送 OUT。
    3. 把原料 / BUF 的工件送往机床。
       prefer_no_setup: 优先选 setup_ms=0 的机床
       prefer_fast:     优先选 nominal_ms+setup_ms 最小的
    """
    moves = [a for a in acts if a["kind"] == "move"]
    if not moves:
        return None

    # 1) 卸下已完成工件
    unload = [a for a in moves if parts.get(a["part"], {}).get("on_machine_done")]
    if unload:
        # 优先 OUT，其次 BUF
        for dest in ("OUT", "BUF"):
            m = [a for a in unload if a["to"] == dest]
            if m:
                return m[0]
        return unload[0]

    # 2) 完成品送 OUT
    to_out = [a for a in moves if a["to"] == "OUT"]
    if to_out:
        return to_out[0]

    # 3) 送往机床
    to_machine = [a for a in moves if a["to"] not in ("OUT", "BUF")]
    if to_machine:
        if prefer_no_setup:
            no_setup = [a for a in to_machine if a.get("setup_ms", 0) == 0]
            pool = no_setup if no_setup else to_machine
        else:
            pool = to_machine

        if prefer_fast:
            pool = sorted(pool,
                          key=lambda a: a.get("setup_ms", 0) + a.get("nominal_ms", 0))
        return pool[0]

    return None


def baseline_fifo(obs: dict) -> dict:
    """参考基线：列表顺序第一个 move；没有 move 就 wait。"""
    acts  = obs["legal_actions"]
    parts = obs["parts"]
    wait  = next(a for a in acts if a["kind"] == "wait")
    act   = _priority_move(acts, parts)
    return act if act is not None else wait


def baseline_greedy(obs: dict) -> dict:
    """优先选 setup+nominal 总耗时最短的机床。"""
    acts  = obs["legal_actions"]
    parts = obs["parts"]
    wait  = next(a for a in acts if a["kind"] == "wait")
    act   = _priority_move(acts, parts, prefer_fast=True)
    return act if act is not None else wait


def baseline_batch(obs: dict) -> dict:
    """
    批量策略：优先选无需换型的机床（同类连续加工）；
    同等条件下选总耗时最短的。
    """
    acts  = obs["legal_actions"]
    parts = obs["parts"]
    wait  = next(a for a in acts if a["kind"] == "wait")
    act   = _priority_move(acts, parts, prefer_no_setup=True, prefer_fast=True)
    return act if act is not None else wait


def baseline_worst(obs: dict) -> dict:
    """
    最差基线（0 分锚点）：总是选 setup+nominal 最慢的机床。
    仍保留"卸出已完成工件"的死锁保护逻辑，确保订单能完成。
    用途：作为评分公式的 C_base，使 fifo/greedy 自然落在 40-60 分，
    让 naive AI 策略落在 20-45 分附近。
    """
    acts  = obs["legal_actions"]
    parts = obs["parts"]
    wait  = next(a for a in acts if a["kind"] == "wait")
    moves = [a for a in acts if a["kind"] == "move"]
    if not moves:
        return wait

    # 1) 先卸出 on_machine_done 工件（死锁保护）
    unload = [a for a in moves if parts.get(a["part"], {}).get("on_machine_done")]
    if unload:
        for dest in ("OUT", "BUF"):
            m = [a for a in unload if a["to"] == dest]
            if m:
                return m[0]
        return unload[0]

    # 2) 完成品送 OUT
    to_out = [a for a in moves if a["to"] == "OUT"]
    if to_out:
        return to_out[0]

    # 3) 送往机床：选 setup+nominal 最慢的（故意差）
    to_machine = [a for a in moves if a["to"] not in ("OUT", "BUF")]
    if to_machine:
        return max(to_machine,
                   key=lambda a: a.get("setup_ms", 0) + a.get("nominal_ms", 0))

    return moves[0]
