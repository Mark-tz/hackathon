"""
统一策略评测入口：import 任意策略模块，跑全部场景，输出每族得分。

用法:
    python -I evaluate_policy.py --policy simkit.baselines.simple --fn baseline_batch
    python -I evaluate_policy.py --policy my_policy.py --fn decide

评分设计：
  C_base  = baseline_worst  的 mean_flow_ms（故意最慢机床，锚定 0 分）
  C_ref   = min(fifo/greedy/batch) * 0.80（锚定 100 分）
  score   = 100 × (C_base − C_team) / (C_base − C_ref)

  partial credit：未完成工件使用 horizon 值代入 flow_ms，不再 -100 断崖。
  仿真崩溃（valid=False）→ -100。
"""
import sys, os, argparse, importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from simkit.engine import Engine
from simkit.scenarios import ALL_INSTANCES
from simkit.baselines.simple import baseline_fifo, baseline_greedy, baseline_batch, baseline_worst


def run_once(scenario: dict, policy_fn) -> dict:
    eng = Engine(scenario)
    gen = eng.run()
    try:
        obs = next(gen)
        while True:
            action = policy_fn(obs)
            obs = gen.send(action)
    except StopIteration as e:
        return e.value
    return {"valid": False, "error": "no_return"}


def load_policy(path: str, fn_name: str):
    if path.endswith(".py"):
        spec = importlib.util.spec_from_file_location("policy_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(path)
    return getattr(mod, fn_name)


def compute_base(inst_id: str) -> int:
    """
    C_base = baseline_worst 的 mean_flow_ms（0 分锚点）。
    """
    inst = next(i for i in ALL_INSTANCES if i["id"] == inst_id)
    r = run_once(inst, baseline_worst)
    if r.get("valid"):
        return r["mean_flow_ms"]
    return inst["horizon_ms"]


def compute_ref(inst_id: str) -> int:
    """
    C_ref = min(fifo, greedy, batch) × 0.80（100 分锚点）。
    """
    inst = next(i for i in ALL_INSTANCES if i["id"] == inst_id)
    flows = []
    for fn in [baseline_fifo, baseline_greedy, baseline_batch]:
        r = run_once(inst, fn)
        if r.get("valid"):
            flows.append(r["mean_flow_ms"])
    if not flows:
        return inst["horizon_ms"] // 2
    return int(min(flows) * 0.80)


def _effective_flow(result: dict, inst: dict) -> int:
    """
    计算有效 mean_flow_ms。
    未完成工件按 horizon 计入（partial credit），不再直接 -100。
    仿真崩溃（valid=False）返回 None。
    """
    if not result.get("valid"):
        return None
    # engine 在 mean_flow_ms 中已对未完成订单使用 horizon 惩罚
    return result["mean_flow_ms"]


def score_instance(team_result: dict, base_ms: int, ref_ms: int) -> float:
    """
    score = 100 × (C_base − C_team) / (C_base − C_ref)
    C_base = baseline_worst mean_flow_ms（0 分）
    C_ref  = min(simple baselines) × 0.80（100 分）
    partial credit：未完成订单已在 engine 里用 horizon 值惩罚，直接代入公式。
    仿真崩溃 → -100。
    """
    if not team_result.get("valid"):
        return -100.0
    C_team = team_result["mean_flow_ms"]
    C_base = base_ms
    if C_base <= ref_ms:
        return 0.0
    s = 100.0 * (C_base - C_team) / (C_base - ref_ms)
    return round(max(-100.0, min(150.0, s)), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, help="模块路径或 .py 文件路径")
    ap.add_argument("--fn", default="decide", help="策略函数名")
    ap.add_argument("--label", default=None, help="显示标签")
    args = ap.parse_args()

    policy_fn = load_policy(args.policy, args.fn)
    label = args.label or args.fn

    # 预计算每实例的 base（worst）和 ref（best×0.80）
    refs = {}
    bases = {}
    for inst in ALL_INSTANCES:
        bases[inst["id"]] = compute_base(inst["id"])
        refs[inst["id"]] = compute_ref(inst["id"])

    print(f"{'instance':22s} {'family':6s} {label+'得分':>10s}  "
          f"{'team_ms':>10s} {'base_ms':>10s} {'ref_ms':>8s}  done")
    print("-" * 80)

    by_family: dict[str, list[float]] = {}
    all_scores = []
    for inst in ALL_INSTANCES:
        iid = inst["id"]
        fam = inst["family"]
        team = run_once(inst, policy_fn)
        base = bases[iid]
        ref = refs[iid]
        s = score_instance(team, base, ref)
        by_family.setdefault(fam, []).append(s)
        all_scores.append(s)
        team_ms = team.get("mean_flow_ms", -1)
        done = f"{team.get('orders_done',0)}/{team.get('orders_total',0)}"
        print(f"{iid:22s} {fam:6s} {s:10.2f}  {team_ms:>10} {base:>10} "
              f"{ref:>8}  {done}")

    print("\n── 族均分 ──")
    family_means = []
    for fam in sorted(by_family):
        scores = by_family[fam]
        avg = sum(scores) / len(scores)
        family_means.append(avg)
        print(f"  {fam}: {avg:7.2f}  ({scores})")

    overall = sum(all_scores) / len(all_scores) if all_scores else 0
    worst2 = sorted(family_means)[:2]
    worst2_avg = sum(worst2) / len(worst2) if worst2 else 0
    combined = 0.7 * overall + 0.3 * worst2_avg

    print(f"\n  总均分       : {overall:7.2f}")
    print(f"  最差两族均分 : {worst2_avg:7.2f}")
    print(f"  组合分(0.7+0.3): {combined:7.2f}")


if __name__ == "__main__":
    main()
