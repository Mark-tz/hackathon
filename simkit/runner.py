"""
stdio 协议运行器
─────────────────
用法：
    python3 -I simkit/runner.py --instance dev-F2-101 | python3 my_policy.py

或让评测器直接 import 策略函数：
    python3 -I simkit/runner.py --instance dev-F2-101 --policy simkit.baselines.simple --fn baseline_greedy

消息格式（JSON Lines，每行一个对象）：
    runner → policy : {"type":"init", ...}
    policy → runner : {"type":"ready"}
    runner → policy : {"type":"decide","request_id":N, ...}
    policy → runner : {"request_id":N,"action_id":K}
    runner → policy : {"type":"end","result":{...}}

诊断信息由 runner 写到 stderr，不影响 stdout 协议流。
"""
import sys, os, json, argparse, time, importlib, importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from simkit.engine import Engine
from simkit.scenarios import INSTANCE_BY_ID


def _build_init(sc: dict) -> dict:
    return {
        "type": "init",
        "version": "1.0",
        "instance_id": sc["id"],
        "horizon_ms": sc["horizon_ms"],
        "machines": sc["machines"],
        "part_types": sc["part_types"],
        "travel_ms": sc["travel_ms"],
        "robot": sc["robot"],
        "buffer_cap": sc.get("buffer_cap", 2),
        "setup_ms": sc.get("setup_ms", {}),
        "disruptions": sc.get("disruptions", []),
        "limits": {
            "init_cpu_ms": 2000,
            "decide_cpu_ms": 1000,
            "total_cpu_ms": 20000,
        },
    }


def run_stdio(sc: dict, fin, fout):
    """
    用标准 I/O 协议运行一局。
    fin  : 读取策略回复的文件对象（通常是 sys.stdin）
    fout : 写观测的文件对象（通常是 sys.stdout）
    """
    def send(obj):
        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fout.flush()

    def recv():
        line = fin.readline()
        if not line:
            raise EOFError("policy process closed stdin")
        return json.loads(line.strip())

    send(_build_init(sc))
    ready = recv()
    if ready.get("type") != "ready":
        print(f"[runner] 期望 ready，收到: {ready}", file=sys.stderr)

    eng = Engine(sc)
    gen = eng.run()
    request_id = 0

    try:
        obs = next(gen)
        while True:
            msg = {**obs, "type": "decide", "request_id": request_id}
            send(msg)
            resp = recv()
            if resp.get("request_id") != request_id:
                print(f"[runner] request_id 不匹配: {resp}", file=sys.stderr)
            action = {"action_id": resp["action_id"]}
            request_id += 1
            obs = gen.send(action)
    except StopIteration as e:
        result = e.value
    except EOFError as e:
        result = {"valid": False, "error": str(e)}

    send({"type": "end", "result": result})
    return result


def run_inline(sc: dict, policy_fn):
    """直接调用策略函数，不经过 stdio，用于批量评测。"""
    eng = Engine(sc)
    gen = eng.run()
    try:
        obs = next(gen)
        while True:
            action = policy_fn(obs)
            obs = gen.send(action)
    except StopIteration as e:
        return e.value
    return {"valid": False, "error": "generator ended without return"}


def main():
    ap = argparse.ArgumentParser(description="Simkit stdio 协议运行器")
    ap.add_argument("--instance", required=True, help="实例 ID，如 dev-F2-101")
    ap.add_argument("--policy", default=None, help="策略模块（可选，不填则走 stdio）")
    ap.add_argument("--fn", default="decide", help="策略函数名")
    args = ap.parse_args()

    sc = INSTANCE_BY_ID.get(args.instance)
    if sc is None:
        print(f"[runner] 未知实例: {args.instance}", file=sys.stderr)
        print(f"[runner] 可用实例: {list(INSTANCE_BY_ID.keys())}", file=sys.stderr)
        sys.exit(1)

    if args.policy:
        # 内联模式
        if args.policy.endswith(".py"):
            spec = importlib.util.spec_from_file_location("policy_mod", args.policy)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        else:
            mod = importlib.import_module(args.policy)
        policy_fn = getattr(mod, args.fn)
        result = run_inline(sc, policy_fn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # stdio 模式：从 stdin 读策略回复，写到 stdout
        result = run_stdio(sc, sys.stdin, sys.stdout)
        print(f"[runner] 完成: mean_flow={result.get('mean_flow_ms')}ms "
              f"done={result.get('orders_done')}/{result.get('orders_total')}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
