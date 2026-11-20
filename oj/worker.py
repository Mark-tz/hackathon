"""
评测 worker：从数据库取排队提交，运行仿真，写回分数。
独立进程运行，每 2 秒轮询一次。

运行：python3 oj/worker.py
"""
import sys, os, time, traceback, importlib, importlib.util, pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from oj.db import init_db, next_queued, set_running, set_result
from simkit.engine import Engine
from simkit.baselines.simple import baseline_worst, baseline_fifo, baseline_greedy, baseline_batch
from simkit.scenarios_public import PUBLIC_INSTANCES, PUBLIC_F6
from simkit.visualize import build_html

VIZ_DIR = pathlib.Path(__file__).parent / "viz"
VIZ_DIR.mkdir(exist_ok=True)

BASELINES = [baseline_fifo, baseline_greedy, baseline_batch]


def run_once(sc: dict, policy_fn) -> dict:
    eng = Engine(sc)
    gen = eng.run()
    try:
        obs = next(gen)
        while True:
            obs = gen.send(policy_fn(obs))
    except StopIteration as e:
        return e.value
    return {"valid": False, "error": "no_return"}


def compute_base(sc: dict) -> int:
    r = run_once(sc, baseline_worst)
    return r["mean_flow_ms"] if r.get("valid") else sc["horizon_ms"]


def compute_ref(sc: dict) -> int:
    flows = []
    for fn in BASELINES:
        r = run_once(sc, fn)
        if r.get("valid"):
            flows.append(r["mean_flow_ms"])
    return int(min(flows) * 0.80) if flows else sc["horizon_ms"] // 2


def score_instance(result: dict, base_ms: int, ref_ms: int) -> float:
    if not result.get("valid"):
        return -100.0
    C_team = result["mean_flow_ms"]
    if base_ms <= ref_ms:
        return 0.0
    s = 100.0 * (base_ms - C_team) / (base_ms - ref_ms)
    return round(max(-100.0, min(150.0, s)), 2)


def load_policy(path: str):
    spec = importlib.util.spec_from_file_location("submitted_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.decide


def evaluate(policy_path: str) -> tuple[dict, str | None]:
    """返回 (scores_dict, viz_html_path | None)。"""
    policy_fn = load_policy(policy_path)
    by_family: dict[str, list[float]] = {}
    viz_path = None

    for sc in PUBLIC_INSTANCES:
        fam = sc["family"]
        base_ms = compute_base(sc)
        ref_ms  = compute_ref(sc)
        result  = run_once(sc, policy_fn)
        s = score_instance(result, base_ms, ref_ms)
        by_family.setdefault(fam, []).append(s)

        # 用第一个 F6 实例生成可视化（扰动场景最直观）
        if viz_path is None and fam == "F6" and result.get("valid"):
            try:
                html = build_html(sc, result)
                stem = pathlib.Path(policy_path).stem
                vfile = VIZ_DIR / f"{stem}_F6.html"
                vfile.write_text(html, encoding="utf-8")
                viz_path = str(vfile)
            except Exception:
                pass  # 可视化失败不影响评分

    family_means = {fam: sum(v) / len(v) for fam, v in by_family.items()}
    all_means = list(family_means.values())
    overall = sum(all_means) / len(all_means) if all_means else 0.0
    worst2  = sorted(all_means)[:2]
    worst2_avg = sum(worst2) / 2 if len(worst2) >= 2 else (worst2[0] if worst2 else 0)
    combined = round(0.7 * overall + 0.3 * worst2_avg, 4)

    scores = {
        "F2": round(family_means.get("F2", -100.0), 2),
        "F3": round(family_means.get("F3", -100.0), 2),
        "F6": round(family_means.get("F6", -100.0), 2),
        "combined": combined,
    }
    return scores, viz_path


def run_worker():
    init_db()
    print("[worker] 启动，等待提交...", flush=True)
    while True:
        job = next_queued()
        if not job:
            time.sleep(2)
            continue

        sid = job["id"]
        path = job["policy_path"]
        print(f"[worker] 开始评测 submission #{sid}: {path}", flush=True)
        set_running(sid)
        try:
            scores, viz_path = evaluate(path)
            set_result(sid, scores, viz_path=viz_path)
            print(f"[worker] #{sid} 完成: combined={scores['combined']}"
                  f"{' viz='+viz_path if viz_path else ''}", flush=True)
        except Exception as e:
            err = traceback.format_exc()
            set_result(sid, {}, error=err[:2000])
            print(f"[worker] #{sid} 错误: {e}", flush=True)


if __name__ == "__main__":
    run_worker()
