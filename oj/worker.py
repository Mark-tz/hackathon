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


def _child_evaluate(policy_path: str, result_queue):
    """在子进程中运行，设置资源限制后执行评测。"""
    import resource

    # ── 资源限制 ──────────────────────────────────────────
    try:
        resource.setrlimit(resource.RLIMIT_CPU,   (120, 120))           # CPU 120s
        resource.setrlimit(resource.RLIMIT_AS,    (512<<20, 512<<20))   # 内存 512 MB
        resource.setrlimit(resource.RLIMIT_FSIZE, (8<<20,   8<<20))     # 写文件 8 MB
        resource.setrlimit(resource.RLIMIT_NOFILE,(64,      64))        # fd 数
    except Exception:
        pass

    # ── 清除敏感环境变量，防止策略代码读取 ───────────────
    import os as _os
    for key in list(_os.environ):
        if any(x in key.upper() for x in ("SECRET","TOKEN","KEY","PASS","DB","ADMIN")):
            del _os.environ[key]
    try:
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
            if viz_path is None and fam == "F6" and result.get("valid"):
                try:
                    html = build_html(sc, result)
                    stem = pathlib.Path(policy_path).stem
                    vfile = VIZ_DIR / f"{stem}_F6.html"
                    vfile.write_text(html, encoding="utf-8")
                    viz_path = str(vfile)
                except Exception:
                    pass
        family_means = {fam: sum(v) / len(v) for fam, v in by_family.items()}
        all_means = list(family_means.values())
        overall = sum(all_means) / len(all_means) if all_means else 0.0
        worst2 = sorted(all_means)[:2]
        worst2_avg = sum(worst2) / 2 if len(worst2) >= 2 else (worst2[0] if worst2 else 0)
        combined = round(0.7 * overall + 0.3 * worst2_avg, 4)
        scores = {
            "F2": round(family_means.get("F2", -100.0), 2),
            "F3": round(family_means.get("F3", -100.0), 2),
            "F6": round(family_means.get("F6", -100.0), 2),
            "combined": combined,
        }
        result_queue.put(("ok", scores, viz_path))
    except Exception:
        result_queue.put(("err", traceback.format_exc(), None))


EVAL_TIMEOUT = int(os.environ.get("OJ_EVAL_TIMEOUT", "150"))  # 秒


def evaluate(policy_path: str) -> tuple[dict, str | None]:
    """在独立子进程中评测，wall-clock 超时 EVAL_TIMEOUT 秒。"""
    import multiprocessing
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_child_evaluate, args=(policy_path, q))
    p.start()
    p.join(timeout=EVAL_TIMEOUT)
    if p.is_alive():
        p.terminate()
        p.join(3)
        raise RuntimeError(f"评测超时（>{EVAL_TIMEOUT}s），已终止子进程")
    if p.exitcode != 0:
        raise RuntimeError(f"评测子进程异常退出，exitcode={p.exitcode}")
    if q.empty():
        raise RuntimeError("评测子进程无返回（可能被 OOM killer 终止）")
    status, payload, viz_path = q.get_nowait()
    if status == "err":
        raise RuntimeError(payload)
    return payload, viz_path


def run_worker():
    init_db()
    import os as _os
    uid = _os.getuid() if hasattr(_os, 'getuid') else -1
    if uid == 0:
        print("[worker] 警告：当前以 root 运行，建议切换到低权限用户！", flush=True)
    print(f"[worker] 启动，等待提交（uid={uid}，超时={EVAL_TIMEOUT}s）...", flush=True)
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
