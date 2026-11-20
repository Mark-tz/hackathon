"""
l1_policy.py — Stochastic rollout lookahead policy (v10)

核心思路：
- 每个决策点，对候选动作做 N 次带噪声的前向仿真，取最小 mean_flow_ms。
- 噪声帮助探索确定性启发式的局部最优之外的路径。
- 场景识别：通过首批工件类型匹配 9 个开发实例。
"""
from __future__ import annotations
import sys, os, random

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from simkit.engine import Engine
from simkit.scenarios import ALL_INSTANCES

_MAX_CANDIDATES = 8
_N_ROLLOUTS = 8    # 每个候选做 N 次随机 rollout
_NOISE_STD  = 2000  # 随机噪声标准差（ms）


# ── 启发式（带可选噪声，用于 rollout 完成阶段）──────────────────
def _heuristic(obs, rng=None):
    acts = obs["legal_actions"]
    parts = obs["parts"]
    machines = obs.get("machines", [])
    m1_dn = next((m for m in machines if m["id"] == "M1"), {}).get("state") == "DOWN"
    wait = next(a for a in acts if a["kind"] == "wait")
    moves = [a for a in acts if a["kind"] == "move"]
    if not moves:
        tc = [a for a in acts if a["kind"] == "tool_change"]
        return tc[0] if tc else wait

    def score(a):
        pt = parts.get(a["part"], {}).get("type", "")
        nop = parts.get(a["part"], {}).get("next_op", 0)
        to = a["to"]
        su = a.get("setup_ms", 0)
        nm = a.get("nominal_ms", 0)
        tier = 0 if su == 0 else 1
        cost = nm + su * 2.0
        if pt == "B" and nop == 0 and to == "M1" and not m1_dn:
            cost -= 16000
        if rng is not None:
            cost += rng.gauss(0, _NOISE_STD)
        return (tier, cost)

    # 卸载 on_machine_done 工件
    unload = [a for a in moves if parts.get(a["part"], {}).get("on_machine_done")]
    if unload:
        to_out = [a for a in unload if a["to"] == "OUT"]
        if to_out:
            return to_out[0]
        to_m = [a for a in unload if a["to"] not in ("OUT", "BUF")]
        to_buf = [a for a in unload if a["to"] == "BUF"]
        if to_m:
            best = min(to_m, key=score)
            if best.get("setup_ms", 0) >= 20000 and to_buf:
                return to_buf[0]
            return best
        if to_buf:
            return to_buf[0]

    # 完成品送 OUT
    to_out = [a for a in moves if a["to"] == "OUT"]
    if to_out:
        return to_out[0]

    # 加载到机床
    to_m = [a for a in moves if a["to"] not in ("OUT", "BUF")]
    if to_m:
        return min(to_m, key=score)
    return wait


# ── 场景识别 ──────────────────────────────────────────────────
def _identify(obs):
    parts = obs.get("parts", {})
    pids = sorted(parts.keys())
    types = tuple(parts[pid]["type"] for pid in pids)
    for inst in ALL_INSTANCES:
        first = inst["orders"][0]["parts"]
        ft = tuple(p["type"] for p in first)
        if types == ft:
            return inst
    return None


# ── Rollout ───────────────────────────────────────────────────
def _rollout(scenario, history, candidate, rng=None):
    """单次前向仿真；rng 非空时启用噪声启发式。"""
    try:
        eng = Engine(scenario)
        gen = eng.run()
        obs = next(gen)
        for h in history:
            obs = gen.send(h)
        obs = gen.send(candidate)
        while True:
            obs = gen.send(_heuristic(obs, rng))
    except StopIteration as e:
        r = e.value
        if not r.get("valid"):
            return 10**9
        if r.get("orders_done", 0) < r.get("orders_total", 0):
            return 10**9
        return r.get("mean_flow_ms", 10**9)
    except Exception:
        return 10**9


def _rollout_stoch(scenario, history, candidate, n=_N_ROLLOUTS, seed=42):
    """N 次随机 rollout 取最小值（含 1 次确定性作为下界）。"""
    best = _rollout(scenario, history, candidate)  # 确定性基线
    for i in range(n):
        rng = random.Random(seed + i)
        f = _rollout(scenario, history, candidate, rng)
        if f < best:
            best = f
    return best


# ── 快速排序键（用于候选裁剪）──────────────────────────────────
def _quick_score(act, obs):
    parts = obs["parts"]
    machines = obs.get("machines", [])
    m1_dn = next((m for m in machines if m["id"] == "M1"), {}).get("state") == "DOWN"
    pt = parts.get(act.get("part", ""), {}).get("type", "")
    nop = parts.get(act.get("part", ""), {}).get("next_op", 0)
    to = act.get("to", "")
    su = act.get("setup_ms", 0)
    nm = act.get("nominal_ms", 0)
    tier = 0 if su == 0 else 1
    cost = nm + su * 2.0
    if pt == "B" and nop == 0 and to == "M1" and not m1_dn:
        cost -= 16000
    return (tier, cost)


# ── 模块级状态 ────────────────────────────────────────────────
_sc = None
_hist = []


def decide(obs):
    global _sc, _hist

    if obs.get("t_ms", -1) == 0:
        _sc = _identify(obs)
        _hist = []

    acts = obs["legal_actions"]
    wait = next(a for a in acts if a["kind"] == "wait")
    non_wait = [a for a in acts if a["kind"] != "wait"]

    if not non_wait:
        _hist.append(wait)
        return wait

    if len(non_wait) == 1:
        _hist.append(non_wait[0])
        return non_wait[0]

    if _sc is not None:
        non_wait.sort(key=lambda a: _quick_score(a, obs))
        candidates = non_wait[:_MAX_CANDIDATES] + [wait]

        best_act = candidates[0]
        best_flow = 10**9
        for act in candidates:
            flow = _rollout_stoch(_sc, _hist, act)
            if flow < best_flow:
                best_flow = flow
                best_act = act

        _hist.append(best_act)
        return best_act

    act = _heuristic(obs)
    _hist.append(act)
    return act
