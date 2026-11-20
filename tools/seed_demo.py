"""
seed_demo.py — 在榜单中插入 demo_policy 占位记录，部署后运行一次。

效果：OJ 榜单上会出现一条「演示队伍」的真实评测结果（约 50 分），
让参赛者一进来就能看到有成绩、可以点可视化，不会看到空榜。

用法：
    python3 tools/seed_demo.py
    python3 tools/seed_demo.py --team "演示队伍"   # 自定义队伍名
    python3 tools/seed_demo.py --force            # 删除旧演示记录后重新生成
"""
import os, sys, pathlib, importlib.util, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from oj.db import init_db, get_conn, create_team, enqueue, set_result
from simkit.engine import Engine
from simkit.scenarios_public import PUBLIC_INSTANCES, PUBLIC_F6
from simkit.baselines.simple import baseline_worst, baseline_fifo, baseline_greedy, baseline_batch
from simkit.visualize_3d import build_3d_html

REPO = pathlib.Path(__file__).resolve().parent.parent
VIZ_DIR = REPO / "oj" / "viz"
VIZ_DIR.mkdir(parents=True, exist_ok=True)

BASELINES = [baseline_fifo, baseline_greedy, baseline_batch]


def load_policy(path: str):
    spec = importlib.util.spec_from_file_location("demo_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.decide


def run_once(sc: dict, fn) -> dict:
    eng = Engine(sc)
    gen = eng.run()
    try:
        obs = next(gen)
        while True:
            obs = gen.send(fn(obs))
    except StopIteration as e:
        return e.value
    return {"valid": False}


def compute_base(sc):
    r = run_once(sc, baseline_worst)
    return r["mean_flow_ms"] if r.get("valid") else sc["horizon_ms"]


def compute_ref(sc):
    flows = [run_once(sc, f)["mean_flow_ms"] for f in BASELINES
             if run_once(sc, f).get("valid")]
    return int(min(flows) * 0.80) if flows else sc["horizon_ms"] // 2


def score_instance(result, base_ms, ref_ms):
    if not result.get("valid"):
        return -100.0
    C = result["mean_flow_ms"]
    if base_ms <= ref_ms:
        return 0.0
    return round(max(-100.0, min(150.0, 100.0 * (base_ms - C) / (base_ms - ref_ms))), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="演示队伍", help="榜单上显示的队伍名")
    parser.add_argument("--force", action="store_true", help="删除已有演示记录后重新生成（例如可视化模板更新后）")
    args = parser.parse_args()

    init_db()
    args.phase = "A"  # 比赛只有一个阶段
    policy_path = str(REPO / "demo_policy.py")
    policy_fn = load_policy(policy_path)
    print(f"[seed_demo] 策略: {policy_path}")
    print(f"[seed_demo] 队伍: {args.team}")

    # 创建队伍（已存在时复用）
    team = create_team(args.team)
    print(f"[seed_demo] 队伍 id={team['id']}  token={team['token']}")

    # 检查是否已有 done 记录，避免重复注入
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM submissions WHERE team_id=? AND phase=? AND status='done'",
        (team["id"], args.phase)).fetchone()
    conn.close()
    if existing and args.force:
        conn = get_conn()
        conn.execute("DELETE FROM submissions WHERE team_id=? AND phase=?", (team["id"], args.phase))
        conn.commit(); conn.close()
        print(f"[seed_demo] --force：已删除旧演示记录，重新生成")
        existing = None
    if existing:
        print(f"[seed_demo] 已存在评测记录 #{existing['id']}，跳过（若要重置请手动 DELETE FROM submissions WHERE team_id={team['id']}）")
        return 0

    # 评测
    by_family: dict[str, list[float]] = {}
    viz_path = None

    for sc in PUBLIC_INSTANCES:
        fam = sc["family"]
        base_ms = compute_base(sc)
        ref_ms = compute_ref(sc)
        result = run_once(sc, policy_fn)
        s = score_instance(result, base_ms, ref_ms)
        by_family.setdefault(fam, []).append(s)
        print(f"  {sc['id']:20s}  score={s:6.1f}")

        # 生成 F6 可视化
        if viz_path is None and fam == "F6" and result.get("valid"):
            try:
                html = build_3d_html(sc, result)
                vfile = VIZ_DIR / "demo_team_F6.html"
                vfile.write_text(html, encoding="utf-8")
                viz_path = str(vfile)
                print(f"  → 可视化: {vfile}")
            except Exception as e:
                print(f"  → 可视化生成失败: {e}")

    # 计算综合分
    fm = {fam: sum(v) / len(v) for fam, v in by_family.items()}
    all_m = list(fm.values())
    overall = sum(all_m) / len(all_m)
    worst2 = sorted(all_m)[:2]
    worst2_avg = sum(worst2) / 2 if len(worst2) >= 2 else worst2[0]
    combined = round(0.7 * overall + 0.3 * worst2_avg, 4)
    scores = {
        "F2": round(fm.get("F2", -100.0), 2),
        "F3": round(fm.get("F3", -100.0), 2),
        "F6": round(fm.get("F6", -100.0), 2),
        "combined": combined,
    }

    # 写入数据库（伪造一条 done 状态的提交）
    sid = enqueue(team["id"], args.phase, policy_path, label="演示基线")
    set_result(sid, scores, viz_path=viz_path)

    print(f"\n[seed_demo] 完成 → submission #{sid}")
    print(f"  F2={scores['F2']}  F3={scores['F3']}  F6={scores['F6']}  combined={combined}")
    if viz_path:
        print(f"  可视化链接: /api/viz/{sid}  (需用 token={team['token']} 登录后访问)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
