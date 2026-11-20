"""
可视化工具：甘特图 + 工件流转图
─────────────────────────────
用法：
    python3 -I simkit/visualize.py --instance dev-F2-101 --policy simkit.baselines.l1_policy --fn decide --out gantt.html
    python3 -I simkit/visualize.py --instance dev-F6-301 --out gantt.html   # 用 baseline_greedy
然后在浏览器打开生成的 HTML 文件。

输出内容：
    - 资源甘特图（机床加工 / 换型 / 停机 + 机械臂搬运区间）
    - 工件流转时间线（每个工件各工序的起止）
    - 订单流程时间条形图
    - 关键指标卡（mean_flow_ms、makespan、机械臂利用率、换型次数）
"""
import sys, os, json, argparse, importlib, importlib.util, math

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from simkit.engine import Engine
from simkit.scenarios import INSTANCE_BY_ID
from simkit.baselines.simple import baseline_greedy


# ── 颜色方案 ───────────────────────────────────────────────
PART_COLORS = {
    "A": "#4f8ef7",   # 蓝
    "B": "#f7794f",   # 橙
    "C": "#4fc97f",   # 绿
    "D": "#c94fc9",   # 紫
    "E": "#c9c94f",   # 黄
}
KIND_COLORS = {
    "proc":  None,       # 由工件类型决定
    "setup": "#ffd966",  # 黄
    "down":  "#c0392b",  # 红
    "move":  "#7f8c8d",  # 灰
}
DEFAULT_PART_COLOR = "#aab8c2"


def part_color(ptype: str) -> str:
    return PART_COLORS.get(ptype, DEFAULT_PART_COLOR)


def run_and_collect(sc: dict, policy_fn) -> dict:
    eng = Engine(sc)
    gen = eng.run()
    try:
        obs = next(gen)
        while True:
            obs = gen.send(policy_fn(obs))
    except StopIteration as e:
        return e.value
    return {"valid": False}


def build_html(sc: dict, result: dict) -> str:
    intervals = result.get("intervals", [])
    events    = result.get("events", [])
    order_flow = result.get("order_flow", {})
    horizon   = sc["horizon_ms"]
    t_end     = result.get("t_end_ms", horizon)

    # ── 收集资源列表 ──────────────────────────────────────
    resources = ["ROBOT"] + [m["id"] for m in sc["machines"]]

    # ── 统计指标 ─────────────────────────────────────────
    robot_busy = sum(iv["t_end"] - iv["t_start"]
                     for iv in intervals if iv["resource"] == "ROBOT")
    robot_util = robot_busy / t_end * 100 if t_end > 0 else 0
    n_setup = sum(1 for iv in intervals if iv["kind"] == "setup")
    n_down  = sum(1 for iv in intervals if iv["kind"] == "down")
    mean_flow = result.get("mean_flow_ms", 0)
    done  = result.get("orders_done", 0)
    total = result.get("orders_total", 0)

    # ── 甘特图数据 ────────────────────────────────────────
    gantt_bars = []
    for iv in intervals:
        color = (part_color(iv.get("type", "")) if iv["kind"] in ("proc", "move")
                 else KIND_COLORS.get(iv["kind"], "#999"))
        label = iv.get("part", iv.get("kind", ""))
        tip = (f"{iv['kind'].upper()} {iv.get('part','')} "
               f"({iv.get('from','')}->{iv.get('to','') or iv.get('type','')}) "
               f"{iv['t_start']}–{iv['t_end']} ms")
        gantt_bars.append({
            "resource": iv["resource"],
            "t_start":  iv["t_start"],
            "t_end":    iv["t_end"],
            "color":    color,
            "label":    label,
            "tip":      tip,
        })

    # 停机区间也加到甘特（来自事件）
    for ev in events:
        if ev["kind"] == "machine_down":
            gantt_bars.append({
                "resource": ev["machine"],
                "t_start":  ev["t"],
                "t_end":    ev["t"] + ev["duration_ms"],
                "color":    KIND_COLORS["down"],
                "label":    "DOWN",
                "tip":      f"DOWN {ev['machine']} {ev['t']}–{ev['t']+ev['duration_ms']} ms",
            })

    # ── 订单流程条 ───────────────────────────────────────
    order_bars = []
    for oid, o in result.get("order_flow", {}).items():
        pass  # 在下面重新取
    orders_data = []
    if "orders" in sc:
        for o in sc["orders"]:
            oid = o["id"]
            release = o["release_ms"]
            if oid in order_flow:
                flow = order_flow[oid]
                orders_data.append({
                    "id": oid,
                    "t_start": release,
                    "t_end":   release + flow,
                    "flow_ms": flow,
                })

    # ── 生成 HTML ────────────────────────────────────────
    inst_id   = sc.get("id", "unknown")
    family    = sc.get("family", "")
    t_scale   = t_end if t_end > 0 else 1

    res_idx   = {r: i for i, r in enumerate(resources)}
    row_h     = 32
    chart_h   = len(resources) * row_h + 40
    label_w   = 80
    chart_w   = 1100

    def px(t):
        return label_w + t / t_scale * (chart_w - label_w)

    def bar_svg(iv):
        x  = px(iv["t_start"])
        x2 = px(iv["t_end"])
        w  = max(x2 - x, 1)
        ri = res_idx.get(iv["resource"], 0)
        y  = ri * row_h + 20
        return (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{row_h-4}" '
                f'rx="2" fill="{iv["color"]}" opacity="0.9">'
                f'<title>{iv["tip"]}</title></rect>')

    bars_svg = "\n".join(bar_svg(b) for b in gantt_bars)
    axes_svg = ""
    # 时间刻度：10 刻
    for i in range(11):
        t_tick = int(t_scale * i / 10)
        x = px(t_tick)
        axes_svg += (f'<line x1="{x:.1f}" y1="16" x2="{x:.1f}" y2="{chart_h-4}" '
                     f'stroke="#ccc" stroke-width="0.5"/>'
                     f'<text x="{x:.1f}" y="12" text-anchor="middle" '
                     f'font-size="9" fill="#666">{t_tick//1000}s</text>')
    # 资源标签
    labels_svg = ""
    for r, i in res_idx.items():
        y = i * row_h + row_h // 2 + 20
        labels_svg += (f'<text x="{label_w-4}" y="{y}" text-anchor="end" '
                       f'dominant-baseline="middle" font-size="11" fill="#333">{r}</text>')

    # 订单甘特
    ord_row_h = 24
    ord_h     = len(orders_data) * ord_row_h + 40
    ord_bars_svg = ""
    ord_labels_svg = ""
    for i, o in enumerate(orders_data):
        x  = px(o["t_start"])
        x2 = px(o["t_end"])
        w  = max(x2 - x, 2)
        y  = i * ord_row_h + 20
        ord_bars_svg += (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{ord_row_h-4}" '
                         f'rx="2" fill="#4f8ef7" opacity="0.75">'
                         f'<title>{o["id"]}: flow={o["flow_ms"]}ms</title></rect>')
        ord_labels_svg += (f'<text x="{label_w-4}" y="{y+ord_row_h//2}" '
                           f'text-anchor="end" dominant-baseline="middle" '
                           f'font-size="10" fill="#333">{o["id"]}</text>')

    # 图例
    legend = ""
    for ptype, color in PART_COLORS.items():
        legend += (f'<span style="display:inline-flex;align-items:center;margin-right:12px">'
                   f'<span style="display:inline-block;width:14px;height:14px;'
                   f'background:{color};border-radius:3px;margin-right:4px"></span>'
                   f'工件{ptype}</span>')
    for kind, color in KIND_COLORS.items():
        if color and kind != "proc":
            label = {"setup":"换型","down":"停机","move":"搬运"}[kind]
            legend += (f'<span style="display:inline-flex;align-items:center;margin-right:12px">'
                       f'<span style="display:inline-block;width:14px;height:14px;'
                       f'background:{color};border-radius:3px;margin-right:4px"></span>'
                       f'{label}</span>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>调度结果可视化 · {inst_id}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
          margin: 0; padding: 20px; background: #f5f6fa; color: #333; }}
  h1   {{ font-size: 18px; font-weight: 600; margin: 0 0 4px; }}
  .sub {{ font-size: 13px; color: #888; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card  {{ background: #fff; border-radius: 8px; padding: 14px 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,.08); min-width: 140px; }}
  .card-label {{ font-size: 11px; color: #999; margin-bottom: 2px; }}
  .card-value {{ font-size: 22px; font-weight: 700; color: #333; }}
  .card-unit  {{ font-size: 12px; color: #aaa; margin-left: 2px; }}
  .section {{ background: #fff; border-radius: 8px; padding: 16px 20px;
              box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 20px; }}
  .section h2 {{ font-size: 14px; font-weight: 600; margin: 0 0 12px; color: #555; }}
  svg {{ overflow: visible; display: block; }}
  .legend {{ font-size: 12px; margin-top: 10px; }}
  .invalid {{ color: #c0392b; font-weight: bold; }}
</style>
</head>
<body>
<h1>调度结果可视化</h1>
<div class="sub">实例：<strong>{inst_id}</strong> &nbsp;·&nbsp;
族：<strong>{family}</strong> &nbsp;·&nbsp;
状态：<strong {'class="invalid"' if not result.get("valid") else ''}>
{"⚠ 无效 — " + result.get("error","") if not result.get("valid") else "✓ 有效"}</strong></div>

<div class="cards">
  <div class="card">
    <div class="card-label">平均流程时间</div>
    <div class="card-value">{mean_flow//1000}<span class="card-unit">s</span></div>
  </div>
  <div class="card">
    <div class="card-label">完工时刻</div>
    <div class="card-value">{t_end//1000}<span class="card-unit">s</span></div>
  </div>
  <div class="card">
    <div class="card-label">订单完成</div>
    <div class="card-value">{done}<span class="card-unit">/{total}</span></div>
  </div>
  <div class="card">
    <div class="card-label">机械臂利用率</div>
    <div class="card-value">{robot_util:.1f}<span class="card-unit">%</span></div>
  </div>
  <div class="card">
    <div class="card-label">换型次数</div>
    <div class="card-value">{n_setup}</div>
  </div>
  <div class="card">
    <div class="card-label">停机次数</div>
    <div class="card-value">{n_down}</div>
  </div>
</div>

<div class="section">
  <h2>资源甘特图</h2>
  <div class="legend">{legend}</div>
  <div style="overflow-x:auto;margin-top:10px">
    <svg width="{chart_w}" height="{chart_h}">
      {axes_svg}
      {labels_svg}
      {bars_svg}
    </svg>
  </div>
</div>

<div class="section">
  <h2>订单流程时间</h2>
  <div style="overflow-x:auto">
    <svg width="{chart_w}" height="{max(ord_h, 60)}">
      {axes_svg}
      {ord_labels_svg}
      {ord_bars_svg}
    </svg>
  </div>
</div>

</body>
</html>
"""
    return html


def main():
    ap = argparse.ArgumentParser(description="Simkit 可视化工具")
    ap.add_argument("--instance", required=True)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--fn", default="decide")
    ap.add_argument("--out", default="gantt.html")
    args = ap.parse_args()

    sc = INSTANCE_BY_ID.get(args.instance)
    if sc is None:
        print(f"未知实例: {args.instance}", file=sys.stderr)
        sys.exit(1)

    if args.policy:
        if args.policy.endswith(".py"):
            spec = importlib.util.spec_from_file_location("policy_mod", args.policy)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        else:
            mod = importlib.import_module(args.policy)
        policy_fn = getattr(mod, args.fn)
    else:
        policy_fn = baseline_greedy

    result = run_and_collect(sc, policy_fn)
    html = build_html(sc, result)

    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {out}")
    print(f"mean_flow={result.get('mean_flow_ms')}ms  "
          f"done={result.get('orders_done')}/{result.get('orders_total')}")


if __name__ == "__main__":
    main()
