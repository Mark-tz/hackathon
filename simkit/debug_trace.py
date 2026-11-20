"""调试：追踪某实例的策略执行时间线。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simkit.engine import Engine
from simkit.scenarios import ALL_INSTANCES, INSTANCE_BY_ID

def load_policy(path, fn):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pm", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, fn)

decide = load_policy("simkit/baselines/l1_policy.py", "decide")

def trace(iid, verbose=False):
    inst = INSTANCE_BY_ID[iid]
    eng = Engine(inst)
    gen = eng.run()
    steps = 0
    moves_log = []
    try:
        obs = next(gen)
        while True:
            t = obs["t_ms"]
            # 简要状态
            ms = {m["id"]: (m["state"], m.get("part"), m.get("setup_from")) for m in obs["machines"]}
            act = decide(obs)
            kind = act["kind"]
            desc = ""
            if kind == "move":
                desc = f"{act.get('part')} {act.get('from')}->{act.get('to')} setup={act.get('setup_ms')} nom={act.get('nominal_ms')}"
                moves_log.append((t, desc))
            elif kind == "wait":
                desc = "WAIT"
            if verbose:
                print(f"t={t:>8} {kind:12s} {desc:40s} M1={ms['M1']} M2={ms['M2']} M3={ms['M3']}")
            obs = gen.send(act)
            steps += 1
    except StopIteration as e:
        res = e.value
        print(f"\nRESULT {iid}: valid={res.get('valid')} mean_flow={res.get('mean_flow_ms')} "
              f"done={res.get('orders_done')}/{res.get('orders_total')} t_end={res.get('t_end_ms')} steps={steps}")
        return res, moves_log
    return None, moves_log

if __name__ == "__main__":
    iid = sys.argv[1] if len(sys.argv) > 1 else "dev-F3-201"
    verbose = "-v" in sys.argv
    res, mlog = trace(iid, verbose)
    print(f"\n=== {len(mlog)} moves ===")
    for t, d in mlog:
        print(f"  t={t:>8}  {d}")
