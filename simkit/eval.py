"""
评测脚本：批量跑基线，输出每族得分摘要。
用法：python eval.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from simkit.engine import Engine
from simkit.scenarios import ALL_INSTANCES
from simkit.baselines.simple import baseline_fifo, baseline_greedy, baseline_batch


def run_once(scenario: dict, policy_fn) -> dict:
    """跑一局，返回 result dict。"""
    eng = Engine(scenario)
    gen = eng.run()
    obs = None
    try:
        obs = next(gen)
        while True:
            action = policy_fn(obs)
            obs = gen.send(action)
    except StopIteration as e:
        return e.value
    return {"valid": False, "error": "generator_ended_without_return"}


def score(result_team: dict, result_base: dict, ref_ms: int) -> float:
    """
    缺口闭合率得分。
    C_base = baseline mean_flow_ms
    C_ref  = ref_ms（全知参照解，这里用估算值）
    C_team = team mean_flow_ms
    s = 100 * (C_base - C_team) / (C_base - C_ref)
    无效局 → -100
    """
    if not result_team.get("valid", False):
        return -100.0
    C_team = result_team["mean_flow_ms"]
    C_base = result_base["mean_flow_ms"]
    if C_base <= ref_ms:
        return 0.0
    s = 100.0 * (C_base - C_team) / (C_base - ref_ms)
    return round(max(-100.0, s), 2)


def main():
    policies = {
        "fifo":   baseline_fifo,
        "greedy": baseline_greedy,
        "batch":  baseline_batch,
    }

    # 收集每族、每策略的原始 mean_flow_ms
    rows = []
    for inst in ALL_INSTANCES:
        iid    = inst["id"]
        family = inst["family"]
        res = {}
        for name, fn in policies.items():
            r = run_once(inst, fn)
            res[name] = r
            status = "OK" if r.get("valid") else f"ERR:{r.get('error')}"
            mf = r.get("mean_flow_ms", -1)
            print(f"  {iid:20s}  {name:8s}  {status}  mean_flow={mf:>8}ms"
                  f"  done={r.get('orders_done',0)}/{r.get('orders_total',0)}")
        rows.append({"id": iid, "family": family, "res": res})

    # 汇总：以 fifo 为基线，greedy 对比
    # ref_ms 估算：比 greedy 再好 25%（粗略占位，校准时替换为 CP-SAT 值）
    print("\n── 得分摘要（greedy vs fifo基线，ref=greedy×0.75）──")
    by_family: dict[str, list[float]] = {}
    for row in rows:
        fam   = row["family"]
        base  = row["res"]["fifo"]
        team  = row["res"]["greedy"]
        # ref 估算
        ref_ms = int(base.get("mean_flow_ms", 0) * 0.75)
        s = score(team, base, ref_ms)
        by_family.setdefault(fam, []).append(s)
        print(f"  {row['id']:20s}  greedy_score={s:7.2f}  "
              f"fifo={base.get('mean_flow_ms',-1)}ms  "
              f"greedy={team.get('mean_flow_ms',-1)}ms")

    print("\n── 族均分 ──")
    for fam, scores in sorted(by_family.items()):
        avg = sum(scores) / len(scores)
        print(f"  {fam}: {avg:.2f}  ({scores})")

    # 额外：输出 fifo 自身结果，确认引擎正确
    print("\n── fifo 原始指标（用于引擎正确性检查）──")
    for row in rows:
        r = row["res"]["fifo"]
        print(f"  {row['id']:20s}  mean_flow={r.get('mean_flow_ms',-1):>8}ms"
              f"  t_end={r.get('t_end_ms',-1):>8}ms"
              f"  done={r.get('orders_done',0)}/{r.get('orders_total',0)}")


if __name__ == "__main__":
    main()
