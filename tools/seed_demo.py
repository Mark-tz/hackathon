"""
seed_demo.py — 生成预置演示可视化数据。

部署后运行一次，用 example_policy 跑一个公开 F6 实例，
把 3D 可视化 HTML 写到 oj/demo_viz/，让访问者在无任何提交时
也能直接通过 /demo_viz 看到效果。

用法：
    python3 tools/seed_demo.py
"""
import os, sys, pathlib, importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from simkit.engine import Engine
from simkit.scenarios_public import PUBLIC_F6
from simkit.visualize_3d import build_3d_html

DEMO_DIR = pathlib.Path(__file__).resolve().parent.parent / "oj" / "demo_viz"
DEMO_DIR.mkdir(parents=True, exist_ok=True)


def load_policy(path: str):
    spec = importlib.util.spec_from_file_location("demo_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.decide


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


def main():
    # 用仓库根的 example_policy.py 作为演示策略
    repo = pathlib.Path(__file__).resolve().parent.parent
    policy_path = str(repo / "example_policy.py")
    policy_fn = load_policy(policy_path)

    # 选第一个 F6 公开实例（含扰动，可视化效果最有故事感）
    sc = PUBLIC_F6[0]
    print(f"[seed_demo] 运行 example_policy 于场景 {sc.get('id','?')} ({sc['family']})")
    result = run_once(sc, policy_fn)

    if not result.get("valid"):
        print(f"[seed_demo] 策略返回无效结果: {result.get('error','?')}")
        return 1

    html = build_3d_html(sc, result)
    out = DEMO_DIR / "example_F6.html"
    out.write_text(html, encoding="utf-8")
    print(f"[seed_demo] 已写入 {out} ({out.stat().st_size//1024} KB)")
    print(f"[seed_demo] mean_flow_ms={result.get('mean_flow_ms')}, "
          f"orders_done={result.get('orders_done')}/{result.get('total_orders')}")
    print(f"[seed_demo] 访问 /demo_viz 或 /demo_viz/example_F6.html 查看")
    return 0


if __name__ == "__main__":
    sys.exit(main())
