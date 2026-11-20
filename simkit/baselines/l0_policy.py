"""
L0 一次性策略：FIFO 卸载顺序 + urgency 装载排序 + 无死锁

卸载优先级：
1. on_machine_done -> OUT（全工序完成，立即清走）
2. on_machine_done -> BUF（有空位则缓冲，快速释放机器人）
3. on_machine_done -> 下一台机床（BUF 满时直接转运）

装载优先级（IN/BUF -> 机床）：
- urgency = 该工件当前工序「最慢兼容机床 - 最快兼容机床」nominal 差值。
  差值越大，说明最快机床是稀缺瓶颈；高 urgency 工件先占用。
  F2 中 B 型工件 urgency=(44000-28000)=16000，A/C 均为 6000，
  所以 B 自动争抢 M1，把 A/C 推向其他机床。
- 同等 urgency 下 no_setup 优先（F3 同类批量减换型）。
- 再按 setup_ms + nominal_ms 最小排序。

保证：只要有等待工件且有空闲兼容机床，步骤 4 必然装载，不会死锁。
"""
from __future__ import annotations

_OPS: dict[str, list[dict]] = {
    "A": [{"M1": 30000, "M2": 36000},
          {"M3": 24000, "M2": 28000}],
    "B": [{"M1": 28000, "M3": 44000},
          {"M2": 22000, "M3": 26000}],
    "C": [{"M2": 32000, "M3": 32000},
          {"M1": 20000, "M2": 25000}],
}


def _urgency(ptype: str, next_op: int) -> int:
    ops = _OPS.get(ptype)
    if not ops or next_op >= len(ops):
        return 0
    noms = list(ops[next_op].values())
    return max(noms) - min(noms)


def decide(obs: dict) -> dict:
    acts = obs["legal_actions"]
    parts = obs["parts"]
    mach_ids = {m["id"] for m in obs["machines"]}
    wait = next((a for a in acts if a["kind"] == "wait"), None)

    moves = [a for a in acts if a["kind"] == "move"]
    if not moves:
        return wait if wait is not None else acts[-1]

    done_pids = {pid for pid, p in parts.items() if p.get("on_machine_done")}

    # ── 1. on_machine_done -> OUT ─────────────────────────
    done_to_out = [a for a in moves if a["part"] in done_pids and a["to"] == "OUT"]
    if done_to_out:
        return done_to_out[0]

    # ── 2. on_machine_done -> BUF ─────────────────────────
    done_to_buf = [a for a in moves if a["part"] in done_pids and a["to"] == "BUF"]
    if done_to_buf:
        return done_to_buf[0]

    # ── 3. on_machine_done -> 下一台机床（BUF 满时）────────
    done_to_mach = [a for a in moves if a["part"] in done_pids and a["to"] in mach_ids]
    if done_to_mach:
        no_setup = [a for a in done_to_mach if a.get("setup_ms", 0) == 0]
        pool = no_setup if no_setup else done_to_mach
        return min(pool, key=lambda a: a.get("setup_ms", 0) + a.get("nominal_ms", 0))

    # ── 4. IN/BUF -> 机床（urgency 排序）─────────────────
    loads = [a for a in moves if a["to"] in mach_ids and a["part"] not in done_pids]
    if loads:
        by_part: dict[str, list] = {}
        for a in loads:
            by_part.setdefault(a["part"], []).append(a)

        best_action = None
        best_key: tuple = (float("inf"), float("inf"), float("inf"))

        for pid, cands in by_part.items():
            p = parts[pid]
            urg = _urgency(p["type"], p["next_op"])

            no_setup = [a for a in cands if a.get("setup_ms", 0) == 0]
            pool = no_setup if no_setup else cands
            best_for_part = min(
                pool,
                key=lambda a: a.get("setup_ms", 0) + a.get("nominal_ms", 0)
            )

            has_setup = int(best_for_part.get("setup_ms", 0) > 0)
            total = best_for_part.get("setup_ms", 0) + best_for_part.get("nominal_ms", 0)
            key = (-urg, has_setup, total)
            if key < best_key:
                best_key = key
                best_action = best_for_part

        if best_action is not None:
            return best_action

    # ── 5. 兜底 wait ──────────────────────────────────────
    return wait if wait is not None else acts[-1]
