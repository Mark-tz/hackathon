"""
3D 策略动画（Three.js 自包含 HTML，需联网加载 CDN）

场景：1 台关节机械臂居中，数控机床呈弧形排布，原料区 / 缓冲区 / 成品区为料台。
页面内含：解说卡片、当前动作字幕、订单进度面板、甘特时间轴（可点击拖动跳转）。

用法：
    from simkit.visualize_3d import build_3d_html
    html = build_3d_html(sc, result)
CLI:
    python3 simkit/visualize_3d.py --instance dev-F6-301 --policy example_policy.py --out out_3d.html
"""
import json, pathlib


def _travel(sc: dict, a: str, b: str) -> int:
    if a == b:
        return 0
    tm = sc.get("travel_ms", {})
    return tm.get(a, {}).get(b, tm.get(b, {}).get(a, 0))


def _alloc(used: set) -> int:
    i = 0
    while i in used:
        i += 1
    used.add(i)
    return i


def build_viz_data(sc: dict, result: dict) -> dict:
    """把仿真结果整理成前端可直接按时间 t 求值的数据。"""
    machines = [m["id"] for m in sc.get("machines", [])]
    part_types = sc.get("part_types", {})

    # ── 工件 / 订单 ──
    parts, pidx, orders = [], {}, []
    flow = result.get("order_flow", {})
    for o in sc.get("orders", []):
        idxs = []
        for p in o["parts"]:
            pidx[p["id"]] = len(parts)
            idxs.append(len(parts))
            parts.append({"id": p["id"], "type": p["type"], "order": o["id"],
                          "rel": o["release_ms"], "in_slot": 0, "done": [], "out_t": None,
                          "nops": len(part_types.get(p["type"], {}).get("ops", [])) or 2})
        orders.append({"id": o["id"], "rel": o["release_ms"], "parts": idxs,
                       "done_t": o["release_ms"] + flow[o["id"]] if o["id"] in flow else None})

    ivs = result.get("intervals", [])

    # ── 机床区间：按停机截断，补出换型后未记录的加工段 ──
    downs = {m: sorted((iv["t_start"], iv["t_end"]) for iv in ivs
                       if iv["resource"] == m and iv["kind"] == "down") for m in machines}

    def clip(mid, t0, t1):
        for d0, _ in downs.get(mid, []):
            if t0 < d0 < t1:
                return d0, True
        return t1, False

    miv = {m: [{"k": "down", "t0": a, "t1": b, "p": -1, "op": 0, "ab": False}
               for a, b in downs[m]] for m in machines}
    ops_done: dict[str, int] = {}
    work = sorted((iv for iv in ivs if iv["resource"] in miv and iv["kind"] in ("proc", "setup")),
                  key=lambda iv: iv["t_start"])
    for iv in work:
        mid, pid = iv["resource"], iv.get("part")
        if pid not in pidx:
            continue
        op = iv.get("op", ops_done.get(pid, 0))
        t1, ab = clip(mid, iv["t_start"], iv["t_end"])
        miv[mid].append({"k": iv["kind"], "t0": iv["t_start"], "t1": t1, "p": pidx[pid], "op": op, "ab": ab})
        if iv["kind"] == "setup" and not ab:
            # 引擎只记录了换型段，随后的加工段按名义工时补出
            try:
                nom = part_types[parts[pidx[pid]]["type"]]["ops"][op]["compat"][mid]
            except (KeyError, IndexError):
                nom = 0
            if nom > 0:
                p1, pab = clip(mid, iv["t_end"], iv["t_end"] + nom)
                miv[mid].append({"k": "proc", "t0": iv["t_end"], "t1": p1, "p": pidx[pid], "op": op, "ab": pab})
                if not pab:
                    ops_done[pid] = op + 1
                    parts[pidx[pid]]["done"].append(p1)
        elif iv["kind"] == "proc" and not ab:
            ops_done[pid] = op + 1
            parts[pidx[pid]]["done"].append(t1)
    for m in miv:
        miv[m].sort(key=lambda v: v["t0"])
    for p in parts:
        p["done"].sort()

    # ── 机械臂搬运：拆成 接近 / 取件 / 运送 / 放件 四段 ──
    rb = sc.get("robot", {})
    pos = rb.get("start_pos", "IN")
    moves = []
    for iv in sorted((iv for iv in ivs if iv["resource"] == "ROBOT" and iv["kind"] == "move"),
                     key=lambda iv: iv["t_start"]):
        if iv.get("part") not in pidx:
            continue
        ph = [_travel(sc, pos, iv["from"]), rb.get("pick_ms", 0),
              _travel(sc, iv["from"], iv["to"]), rb.get("place_ms", 0)]
        dur, tot = iv["t_end"] - iv["t_start"], sum(ph)
        ph = [x * dur / tot for x in ph] if tot > 0 else [0, dur / 2, 0, dur / 2]
        moves.append({"p": pidx[iv["part"]], "from": iv["from"], "to": iv["to"], "fs": 0, "ts": 0,
                      "t0": iv["t_start"], "t1": iv["t_end"], "ph": [round(x, 1) for x in ph],
                      "pk": iv["t_start"] + ph[0] + ph[1] / 2, "pl": iv["t_end"] - ph[3] / 2})
        pos = iv["to"]

    # ── 料位分配（原料区 / 缓冲区取最小空位，成品区顺序摆放）──
    ev = [(p["rel"], 0, "rel", i) for i, p in enumerate(parts)]
    ev += [(m["pk"], 1, "pick", i) for i, m in enumerate(moves)]
    ev += [(m["pl"], 2, "place", i) for i, m in enumerate(moves)]
    used_in, used_buf, buf_of, out_n = set(), set(), {}, 0
    for _, _, kind, i in sorted(ev):
        if kind == "rel":
            parts[i]["in_slot"] = _alloc(used_in)
        elif kind == "pick":
            m = moves[i]
            if m["from"] == "IN":
                m["fs"] = parts[m["p"]]["in_slot"]
                used_in.discard(m["fs"])
            elif m["from"] == "BUF":
                m["fs"] = buf_of.pop(m["p"], 0)
                used_buf.discard(m["fs"])
        else:
            m = moves[i]
            if m["to"] == "BUF":
                m["ts"] = buf_of[m["p"]] = _alloc(used_buf)
            elif m["to"] == "OUT":
                m["ts"], out_n = out_n, out_n + 1
                parts[m["p"]]["out_t"] = m["pl"]

    return {
        "sid": sc.get("id", ""), "family": sc.get("family", ""),
        "t_end": result.get("t_end_ms") or sc.get("horizon_ms", 1_800_000),
        "mean_flow": result.get("mean_flow_ms"), "valid": bool(result.get("valid")),
        "orders_done": result.get("orders_done", 0),
        "orders_total": result.get("orders_total", len(orders)),
        "buf_cap": sc.get("buffer_cap", 12), "machines": machines,
        "start_pos": rb.get("start_pos", "IN"),
        "parts": parts, "orders": orders, "moves": moves, "miv": miv,
        "events": result.get("events", []),
    }


def build_3d_html(sc: dict, result: dict) -> str:
    data = json.dumps(build_viz_data(sc, result), ensure_ascii=False, separators=(",", ":"))
    return _HTML_TEMPLATE.replace("__DATA_JSON__", data.replace("</", "<\\/"))


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略动画 · 单臂柔性机加单元调度</title>
<style>
:root{--bg:#070b14;--panel:rgba(12,19,34,.86);--line:rgba(148,163,184,.16);--text:#e8eef9;--muted:#8a9bb8;
--A:#4cc9f0;--B:#fbbf24;--C:#f472b6;--ok:#34d399;--warn:#fb923c;--err:#ef4444;--wait:#93c5fd;--idle:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;background:var(--bg);color:var(--text);overflow:hidden;font-size:13px}
#app{display:grid;grid-template-columns:1fr 350px;grid-template-rows:1fr 178px;height:100vh}
#stage{grid-area:1/1;position:relative;overflow:hidden;background:radial-gradient(ellipse at 50% 35%,#13203a 0%,#070b14 72%)}
#stage canvas{display:block}
#side{grid-area:1/2/3/3;background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
#dock{grid-area:2/1;background:var(--panel);border-top:1px solid var(--line);padding:10px 16px 8px;display:flex;flex-direction:column;gap:8px;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;backdrop-filter:blur(10px);box-shadow:0 10px 40px rgba(0,0,0,.35)}
/* 解说卡片 */
#intro{position:absolute;left:16px;top:16px;width:318px;padding:14px 16px}
#intro h1{font-size:15px;font-weight:700;letter-spacing:.3px;display:flex;align-items:center;gap:8px}
#intro h1 .fam{font-size:11px;font-weight:600;color:#0b1220;background:var(--A);border-radius:9px;padding:2px 8px}
#intro p{color:#b9c6dc;line-height:1.65;margin-top:8px;font-size:12.5px}
#intro p b{color:#fff}
.flow{display:flex;align-items:center;gap:6px;margin-top:10px;font-size:11.5px;color:var(--muted);flex-wrap:wrap}
.flow span{background:rgba(148,163,184,.12);border-radius:7px;padding:3px 8px;color:#dbe5f5}
.flow i{font-style:normal;opacity:.6}
#intro .x{position:absolute;right:10px;top:8px;cursor:pointer;color:var(--muted);font-size:16px;line-height:1;padding:4px}
#intro.min p,#intro.min .flow{display:none}
/* 字幕 */
#caption{position:absolute;left:50%;top:16px;transform:translateX(-50%);padding:9px 18px;font-size:14px;white-space:nowrap;border-radius:22px;max-width:calc(100% - 700px);overflow:hidden;text-overflow:ellipsis}
#caption b{color:#fff}#caption .dim{color:var(--muted)}
#toasts{position:absolute;left:50%;top:66px;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:6px;pointer-events:none}
.toast{padding:7px 16px;border-radius:18px;font-size:13px;font-weight:600;background:rgba(12,19,34,.92);border:1px solid var(--line);animation:tin .25s ease-out;transition:opacity .5s}
.toast.ok{border-color:rgba(52,211,153,.6);color:#a7f3d0}.toast.err{border-color:rgba(239,68,68,.7);color:#fecaca}.toast.info{border-color:rgba(76,201,240,.5);color:#bae6fd}
@keyframes tin{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
/* 3D 标签 */
#labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}
.lb{position:absolute;left:0;top:0;transform:translate(-50%,-100%);white-space:nowrap;background:rgba(9,14,26,.84);border:1px solid var(--line);border-radius:10px;padding:5px 10px 6px;font-size:12px;text-align:center;will-change:transform}
.lb b{font-size:13px;margin-right:6px}
.lb .st{color:var(--muted)}
.lb .pb{height:3px;border-radius:2px;background:rgba(148,163,184,.2);margin-top:5px;overflow:hidden;display:none}
.lb .pb i{display:block;height:100%;width:0;background:var(--ok)}
.lb[data-s=proc]{border-color:rgba(52,211,153,.55)}.lb[data-s=proc] .st{color:#a7f3d0}.lb[data-s=proc] .pb{display:block}
.lb[data-s=setup]{border-color:rgba(251,146,60,.6)}.lb[data-s=setup] .st{color:#fed7aa}.lb[data-s=setup] .pb{display:block}.lb[data-s=setup] .pb i{background:var(--warn)}
.lb[data-s=down]{border-color:rgba(239,68,68,.8);background:rgba(60,10,14,.88)}.lb[data-s=down] .st{color:#fecaca;font-weight:700}
.lb[data-s=wait]{border-color:rgba(147,197,253,.6)}.lb[data-s=wait] .st{color:#bfdbfe}
.lb.zone b{margin:0}.lb.zone .st{margin-left:6px}
/* 侧栏 */
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:14px 14px 10px}
.kpi{background:rgba(148,163,184,.08);border-radius:10px;padding:9px 10px}
.kpi span{display:block;color:var(--muted);font-size:11px}
.kpi b{display:block;font-size:19px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}
.kpi b small{font-size:11px;font-weight:500;color:var(--muted);margin-left:2px}
.sec{padding:8px 14px;color:var(--muted);font-size:11px;font-weight:600;letter-spacing:1px;display:flex;justify-content:space-between}
.legend{display:flex;flex-wrap:wrap;gap:6px 12px;padding:0 14px 10px;font-size:12px;color:#c5d1e6}
.legend div{display:flex;align-items:center;gap:6px}
.sh{width:12px;height:12px;display:inline-block;flex:none;background:var(--c,#cbd5e1)}
.sh.A{border-radius:50%}.sh.B{border-radius:2px}.sh.C{clip-path:polygon(25% 3%,75% 3%,100% 50%,75% 97%,25% 97%,0 50%)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;box-shadow:0 0 8px currentColor;background:currentColor}
#orders{flex:1;overflow-y:auto;padding:0 14px 14px;min-height:0}
.ord{border:1px solid var(--line);border-radius:12px;padding:10px 11px;margin-bottom:9px;transition:opacity .3s,border-color .3s}
.ord[data-s=pending]{opacity:.38}
.ord[data-s=done]{border-color:rgba(52,211,153,.5);background:rgba(52,211,153,.06)}
.ord .h{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.ord .h b{font-size:13px}
.ord .h span{font-size:11px;color:var(--muted)}
.ord[data-s=active] .h span{color:#fde68a}.ord[data-s=done] .h span{color:#6ee7b7;font-weight:600}
.pc{display:grid;grid-template-columns:14px 44px 34px 1fr;align-items:center;gap:7px;padding:3px 0;font-size:12px}
.pc .pips{display:flex;gap:3px}
.pc .pips u{width:13px;height:5px;border-radius:3px;background:rgba(148,163,184,.25);text-decoration:none}
.pc .pips u.on{background:var(--c)}.pc .pips u.cur{background:var(--c);animation:bl .7s infinite alternate}
@keyframes bl{from{opacity:.35}to{opacity:1}}
.pc small{color:var(--muted);font-size:11px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc[data-l=OUT] small{color:#6ee7b7}.pc[data-l=ROBOT] small{color:#fdba74}
/* 底部时间轴 */
.ctl{display:flex;align-items:center;gap:10px}
.ctl button,.ctl select{background:rgba(148,163,184,.12);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 13px;font-size:13px;cursor:pointer;font-family:inherit}
.ctl button:hover{background:rgba(148,163,184,.22)}
.ctl button.pri{background:#2563eb;border-color:#3b82f6;font-weight:600;min-width:84px}
.ctl .time{margin-left:auto;font-variant-numeric:tabular-nums;color:#c5d1e6;font-size:13px}
.ctl .hint{color:var(--muted);font-size:11.5px}
#gantt{flex:1;width:100%;min-height:0;cursor:pointer;border-radius:8px}
/* 结束卡片 / 加载 */
#end{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);padding:24px 34px;text-align:center;display:none}
#end h2{font-size:19px;margin-bottom:6px}#end .big{font-size:38px;font-weight:800;color:#6ee7b7;font-variant-numeric:tabular-nums}
#end .big small{font-size:15px;color:var(--muted);font-weight:500;margin-left:4px}
#end p{color:var(--muted);margin:4px 0 14px}
#end button{background:#2563eb;color:#fff;border:0;border-radius:9px;padding:9px 22px;font-size:14px;font-weight:600;cursor:pointer}
.loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:14px;text-align:center;padding:24px}
@media(max-width:1100px){#app{grid-template-columns:1fr}#side{display:none}#intro{display:none}#caption{max-width:calc(100% - 32px)}}
</style></head><body>
<div id="app">
<div id="stage">
  <div class="loading" id="load">正在加载 3D 场景…</div>
  <div id="labels"></div>
  <div class="card" id="intro">
    <span class="x" id="intro-x" title="收起/展开">–</span>
    <h1>🏭 单臂柔性机加单元 <span class="fam" id="fam"></span></h1>
    <p>订单陆续到达，每个工件要先后在机床上完成 <b>2 道工序</b>才能出货。全场只有 <b>1 台机械臂</b>负责搬运——策略要实时决定<b>「下一步搬哪个工件、送去哪台机床」</b>，目标是让订单<b>尽快交付</b>。</p>
    <div class="flow"><span>原料区</span><i>→</i><span>工序 1</span><i>→</i><span>工序 2</span><i>→</i><span>成品区</span><i>（缓冲区可暂存）</i></div>
  </div>
  <div class="card" id="caption">准备开始…</div>
  <div id="toasts"></div>
  <div class="card" id="end"><h2 id="end-h"></h2><div class="big" id="end-v"></div><p id="end-p"></p><button id="end-btn">⟲ 重新播放</button></div>
</div>
<div id="side">
  <div class="kpis">
    <div class="kpi"><span>仿真时间</span><b id="k-t">0<small>s</small></b></div>
    <div class="kpi"><span>订单交付</span><b id="k-o">0/0</b></div>
    <div class="kpi"><span>平均流程时间</span><b id="k-f">—</b></div>
  </div>
  <div class="sec"><span>图例</span></div>
  <div class="legend" id="lg-types"></div>
  <div class="legend">
    <div><span class="dot" style="color:var(--ok)"></span>加工中</div>
    <div><span class="dot" style="color:var(--warn)"></span>换型中</div>
    <div><span class="dot" style="color:var(--wait)"></span>待取件</div>
    <div><span class="dot" style="color:var(--err)"></span>停机</div>
  </div>
  <div class="sec"><span>订单进度</span><span style="font-weight:400;letter-spacing:0">▮▮ = 两道工序</span></div>
  <div id="orders"></div>
</div>
<div id="dock">
  <div class="ctl">
    <button class="pri" id="play">▶ 播放</button>
    <button id="reset">⟲ 重播</button>
    <select id="speed"><option value="5">5×</option><option value="10">10×</option><option value="20">20×</option><option value="50" selected>50×</option><option value="100">100×</option><option value="200">200×</option></select>
    <span class="hint">拖动画面旋转视角 · 点击时间轴跳转 · 空格键 播放/暂停</span>
    <span class="time" id="time">0 s / 0 s</span>
  </div>
  <canvas id="gantt"></canvas>
</div>
</div>
<script type="importmap">
{"imports":{
  "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
}}
</script>
<script>
const DATA = __DATA_JSON__;
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const D = DATA, $ = id => document.getElementById(id);
const tEnd = Math.max(1, D.t_end), moves = D.moves, parts = D.parts;
const TYPE_COL = {A:'#4cc9f0',B:'#fbbf24',C:'#f472b6',D:'#a78bfa',E:'#86efac',F:'#fca5a5'};
const tcol = t => TYPE_COL[t] || '#cbd5e1';
const LOCN = {IN:'原料区',OUT:'成品区',BUF:'缓冲区'}, locName = l => LOCN[l] || l;
const FAM = {F2:'瓶颈机床',F3:'高换型代价',F6:'机床随机停机'};
const ST_COL = {proc:0x34d399,setup:0xfb923c,down:0xef4444,wait:0x93c5fd,idle:0x334155};
const sec = ms => (ms/1000).toFixed(0);
const clamp01 = x => Math.max(0, Math.min(1, x));
const ease = x => { x = clamp01(x); return x*x*(3-2*x); };
const lerp = (a,b,k) => a+(b-a)*k;
function lerpA(a,b,k){ let d=((b-a+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI; return a+d*k; }
parts.forEach(p => p.mv = []); moves.forEach((m,i) => parts[m.p].mv.push(i));

// ═══ 场景基础 ═══════════════════════════════════════════
const stage = $('stage');
const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true, powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(devicePixelRatio||1, 2));
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
stage.insertBefore(renderer.domElement, stage.firstChild);
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x070b14, 18, 42);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 120);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.0, 0); controls.enableDamping = true; controls.dampingFactor = .08;
controls.maxPolarAngle = 1.45; controls.minDistance = 6; controls.maxDistance = 30;
let userCam = false; controls.addEventListener('start', () => userCam = true);

scene.add(new THREE.HemisphereLight(0xbcd3ff, 0x0a1020, .45));
const sun = new THREE.DirectionalLight(0xffffff, 1.8); sun.position.set(6, 13, 8); sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; sun.shadow.normalBias = .02;
Object.assign(sun.shadow.camera, {left:-10,right:10,top:10,bottom:-10,near:1,far:40}); scene.add(sun);
const rim = new THREE.DirectionalLight(0x4f9dff, .9); rim.position.set(-9, 6, -9); scene.add(rim);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1,1), .6, .7, 1.05); composer.addPass(bloom);
composer.addPass(new OutputPass());

// ═══ 建模工具 ═══════════════════════════════════════════
const SM = (c,o={}) => new THREE.MeshStandardMaterial({color:c, roughness:.5, metalness:.4, ...o});
const EM = (c,i=2) => new THREE.MeshStandardMaterial({color:0x0b0f18, emissive:c, emissiveIntensity:i, roughness:.4});
const box = (w,h,d) => new THREE.BoxGeometry(w,h,d);
const cyl = (r1,r2,h,s=28) => new THREE.CylinderGeometry(r1,r2,h,s);
const cylZ = (r,h) => cyl(r,r,h).rotateX(Math.PI/2);
function mk(geo, mat, x, y, z, parent, shadow=true){ const m = new THREE.Mesh(geo, mat); m.position.set(x,y,z);
  m.castShadow = shadow; m.receiveShadow = true; parent.add(m); return m; }
const polar = (deg, r) => { const a = deg*Math.PI/180; return new THREE.Vector3(r*Math.sin(a), 0, -r*Math.cos(a)); };
function textTex(txt, fg='#e8eef9', bg='rgba(0,0,0,0)', w=256, h=96, font='bold 64px sans-serif'){
  const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
  x.fillStyle = bg; x.fillRect(0,0,w,h); x.fillStyle = fg; x.font = font; x.textAlign = 'center'; x.textBaseline = 'middle';
  x.fillText(txt, w/2, h/2+3); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t; }

const matShell = SM(0xe6ebf2, {roughness:.38, metalness:.55});
const matBase  = SM(0x27324a, {roughness:.6, metalness:.5});
const matDark  = SM(0x131a28, {roughness:.5, metalness:.6});
const matSteel = SM(0xb8c2d0, {roughness:.25, metalness:.95});
const matGlass = new THREE.MeshPhysicalMaterial({color:0x9fd3ff, roughness:.08, metalness:0, transmission:.85, transparent:true, opacity:.38, thickness:.05});
const matOrange= SM(0xff7a1a, {roughness:.42, metalness:.35});
const matRubber= SM(0x0d1118, {roughness:.85, metalness:.1});

// ═══ 地面 ═══════════════════════════════════════════════
(function floor(){
  const c = document.createElement('canvas'); c.width = c.height = 512; const x = c.getContext('2d');
  x.fillStyle = '#121a2b'; x.fillRect(0,0,512,512);
  x.strokeStyle = 'rgba(120,150,200,.10)'; x.lineWidth = 2; x.strokeRect(0,0,512,512);
  x.strokeStyle = 'rgba(120,150,200,.045)'; x.lineWidth = 1;
  for(let i=128;i<512;i+=128){ x.beginPath(); x.moveTo(i,0); x.lineTo(i,512); x.moveTo(0,i); x.lineTo(512,i); x.stroke(); }
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(30,30); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  const f = new THREE.Mesh(new THREE.CircleGeometry(45, 64), new THREE.MeshStandardMaterial({map:t, roughness:.55, metalness:.35}));
  f.rotation.x = -Math.PI/2; f.receiveShadow = true; scene.add(f);
  // 机械臂作业半径警示线
  const dash = new THREE.BoxGeometry(.34,.012,.07), dm = new THREE.MeshStandardMaterial({color:0xfacc15, emissive:0xfacc15, emissiveIntensity:.35, roughness:.6});
  for(let i=0;i<44;i++){ const a = i/44*Math.PI*2, m = new THREE.Mesh(dash, dm); m.position.set(Math.cos(a)*2.35, .008, Math.sin(a)*2.35); m.rotation.y = -a+Math.PI/2; scene.add(m); }
})();

// ═══ 布局 ═══════════════════════════════════════════════
const R_M = 4.25, R_ST = 3.45, R_T = 3.3, PICK_Z = R_M - R_ST, PART_H = .26, TOP_Y = .9;
const nM = D.machines.length, span = nM > 1 ? Math.min(150, 58*(nM-1)) : 0;
const mAngle = i => nM > 1 ? -span/2 + span*i/(nM-1) : 0;
const labelsEl = $('labels'), labels = [];
function addLabel(cls, html, anchor){ const el = document.createElement('div'); el.className = 'lb '+cls; el.innerHTML = html;
  labelsEl.appendChild(el); const o = {el, anchor}; labels.push(o); return o; }

// ── 数控机床 ──
const machines = {};
function buildMachine(id, deg){
  const g = new THREE.Group(); g.position.copy(polar(deg, R_M)); g.lookAt(0,0,0); scene.add(g);
  const W = 2.6, DP = 2.4, F = DP/2;
  mk(box(W,.9,DP), matBase, 0,.45,0, g);
  mk(box(W+.06,.05,DP+.06), matSteel, 0,.925,0, g);
  mk(box(W,1.5,.12), matShell, 0,1.7,-F+.06, g);
  mk(box(.12,1.5,DP), matShell, -W/2+.06,1.7,0, g); mk(box(.12,1.5,DP), matShell, W/2-.06,1.7,0, g);
  mk(box(W,.16,DP), matShell, 0,2.53,0, g);
  mk(box(W-.5,.05,DP-.5), matDark, 0,2.62,0, g); mk(box(.7,.22,.7), matBase, -.6,2.72,-.45, g);
  [-1,1].forEach(sx => { for(let i=0;i<6;i++) mk(box(.02,.035,1.2), matDark, sx*(W/2+.005),1.25+i*.1,-.2, g, false); mk(box(.025,.08,DP), EM(0x38bdf8,.9), sx*(W/2+.008),.98,0, g, false); });
  mk(box(.55,1.5,.14), matShell, -W/2+.275,1.7,F-.07, g); mk(box(.55,1.5,.14), matShell, W/2-.275,1.7,F-.07, g);
  mk(box(W,.36,.18), matDark, 0,2.27,F-.05, g);
  const bar = mk(box(1.5,.06,.03), EM(ST_COL.idle, 2), 0,2.13,F+.045, g, false);
  const plate = mk(new THREE.PlaneGeometry(.78,.26), new THREE.MeshBasicMaterial({map:textTex(id), transparent:true}), 0,2.3,F+.045, g, false);
  plate.receiveShadow = false;
  const doorL = mk(box(.76,1.14,.04), matGlass, -.38,1.52,F+.03, g, false), doorR = mk(box(.76,1.14,.04), matGlass, .38,1.52,F+.03, g, false);
  mk(cyl(.28,.32,.15), matSteel, 0,1.025,PICK_Z, g);
  mk(box(1.3,.04,1.3), matDark, 0,.97,PICK_Z-.25, g);
  const head = new THREE.Group(); head.position.set(0,0,PICK_Z); g.add(head);
  mk(box(.5,.55,.5), matBase, 0,2.17,0, head); mk(cyl(.12,.12,.36), matSteel, 0,1.78,0, head);
  const tool = mk(cyl(.03,.012,.26,12), SM(0xfde68a,{metalness:1,roughness:.2}), 0,1.5,0, head, false);
  const lamp = new THREE.PointLight(0xfff1d6, .6, 5, 1.6); lamp.position.set(0,2.1,PICK_Z+.2); g.add(lamp);
  mk(box(.36,.52,.08), matDark, W/2-.275,1.62,F+.03, g);
  const scr = mk(new THREE.PlaneGeometry(.28,.2), EM(0x38bdf8, 1.6), W/2-.275,1.7,F+.075, g, false);
  mk(cyl(.025,.025,.34,10), matDark, W/2-.3,2.78,-.5, g);
  const tower = [0xef4444,0xfacc15,0x34d399].map((c,i) => mk(cyl(.07,.07,.1,16), EM(c,.05), W/2-.3,2.97+i*.11,-.5, g, false));
  // 切削火花
  const N = 36, pos = new Float32Array(N*3), seed = Array.from({length:N}, () => [Math.random()*6.28, .3+Math.random()*.9, Math.random(), .6+Math.random()]);
  const sg = new THREE.BufferGeometry(); sg.setAttribute('position', new THREE.BufferAttribute(pos,3));
  const sparks = new THREE.Points(sg, new THREE.PointsMaterial({color:0xffc46b, size:.055, transparent:true, opacity:.95, blending:THREE.AdditiveBlending, depthWrite:false}));
  sparks.position.set(0,1.1+PART_H,PICK_Z); sparks.visible = false; g.add(sparks);
  // 停机警示圈
  const ring = new THREE.Mesh(new THREE.RingGeometry(1.95,2.12,64), new THREE.MeshBasicMaterial({color:0xef4444, transparent:true, opacity:.8, side:THREE.DoubleSide}));
  ring.rotation.x = -Math.PI/2; ring.position.y = .02; ring.visible = false; g.add(ring);
  g.updateMatrixWorld(true);
  const pick = g.localToWorld(new THREE.Vector3(0,1.1,PICK_Z));
  const lb = addLabel('m', `<b>${id}</b><span class="st">空闲</span><div class="pb"><i></i></div>`, g.localToWorld(new THREE.Vector3(0,3.45,.4)));
  machines[id] = {g, bar, doorL, doorR, head, tool, lamp, scr, tower, sparks, seed, ring, pick, lb, door:1, state:'', txt:''};
}
D.machines.forEach((id,i) => buildMachine(id, mAngle(i)));

// ── 料台（原料区 / 缓冲区 / 成品区）──
const tables = {}, COLS = [-.66,-.22,.22,.66], ROWS = [.42,0,-.42];
function buildTable(id, deg, color, conveyor){
  const g = new THREE.Group(); g.position.copy(polar(deg, R_T)); g.lookAt(0,0,0); scene.add(g);
  mk(box(2.0,.1,1.5), matDark, 0,TOP_Y-.05,0, g);
  mk(box(2.04,.035,.035), EM(color,1.6), 0,TOP_Y-.03,.765, g, false);
  mk(box(.035,.035,1.5), EM(color,1.0), -1.01,TOP_Y-.03,0, g, false); mk(box(.035,.035,1.5), EM(color,1.0), 1.01,TOP_Y-.03,0, g, false);
  [[-.9,-.65],[.9,-.65],[-.9,.65],[.9,.65]].forEach(([x,z]) => mk(box(.08,TOP_Y-.1,.08), matBase, x,(TOP_Y-.1)/2,z, g));
  mk(box(1.8,.04,1.3), matBase, 0,.3,0, g);
  const pad = new THREE.CircleGeometry(.15, 20), pm = new THREE.MeshBasicMaterial({color, transparent:true, opacity:.16});
  ROWS.forEach(z => COLS.forEach(x => { const p = new THREE.Mesh(pad, pm); p.rotation.x = -Math.PI/2; p.position.set(x,TOP_Y+.004,z); g.add(p); }));
  if(conveyor){ // 通向场外的辊道
    mk(box(1.3,.1,2.6), matBase, 0,.72,-2.1, g); mk(box(.06,.16,2.6), matSteel, -.66,.8,-2.1, g); mk(box(.06,.16,2.6), matSteel, .66,.8,-2.1, g);
    const rg = cyl(.045,.045,1.24,12).rotateZ(Math.PI/2);
    for(let i=0;i<11;i++) mk(rg, matSteel, 0,.8,-.92-i*.235, g, false);
    [[-.5,-1.1],[.5,-1.1],[-.5,-3.1],[.5,-3.1]].forEach(([x,z]) => mk(box(.08,.7,.08), matBase, x,.35,z, g));
    if(conveyor === 'in'){ mk(box(.7,.5,.6), SM(0x8b6b3d,{roughness:.9,metalness:0}), -.1,1.1,-2.0, g); mk(box(.6,.4,.6), SM(0x7a5c33,{roughness:.9,metalness:0}), .15,1.05,-2.85, g); }
    else { mk(box(1.0,.12,.9), SM(0x9a7b4f,{roughness:.9,metalness:0}), 0,.91,-2.3, g); }
  }
  g.updateMatrixWorld(true);
  const lb = addLabel('zone', `<b>${locName(id)}</b><span class="st"></span>`, g.localToWorld(new THREE.Vector3(0,1.75,-.35)));
  tables[id] = {g, lb, txt:'', cache:new Map(), center:g.localToWorld(new THREE.Vector3(0,TOP_Y,0))};
}
buildTable('IN', -128, 0xe2e8f0, 'in'); buildTable('OUT', 128, 0x34d399, 'out'); buildTable('BUF', 180, 0xa78bfa, null);

// 取放点（世界坐标，工件底面所在位置）
function ptOf(loc, slot){
  if(machines[loc]) return machines[loc].pick;
  const tb = tables[loc]; if(!tb) return new THREE.Vector3(0,TOP_Y,2);
  if(!tb.cache.has(slot)){ const k = slot % 12, layer = Math.floor(slot/12);
    tb.cache.set(slot, tb.g.localToWorld(new THREE.Vector3(COLS[k%4], TOP_Y + layer*(PART_H+.02), ROWS[Math.floor(k/4)]))); }
  return tb.cache.get(slot);
}
const anchor = loc => machines[loc] ? machines[loc].pick : (tables[loc] ? tables[loc].center : new THREE.Vector3(0,TOP_Y,2));
const yawOf = p => Math.atan2(-p.z, p.x);

// ── 六轴机械臂（简化为：底座回转 + 大臂 + 小臂 + 垂直夹爪）──
const H0 = 1.25, L1 = 2.0, L2 = 2.0, GRIP = .42, TUCK_R = 1.45, TUCK_Y = 2.75;
const arm = (function(){
  mk(cyl(.7,.82,.3,40), matDark, 0,.15,0, scene); mk(cyl(.52,.6,.34,40), matOrange, 0,.47,0, scene);
  mk(new THREE.TorusGeometry(.76,.025,10,60).rotateX(Math.PI/2), EM(0x38bdf8,2.2), 0,.31,0, scene, false);
  const turret = new THREE.Group(); scene.add(turret);
  mk(cyl(.4,.46,.56,32), matOrange, 0,.92,0, turret); mk(cylZ(.31,.78), matDark, 0,H0,0, turret);
  const upper = new THREE.Group(); upper.position.set(0,H0,0); turret.add(upper);
  mk(box(L1,.3,.36), matOrange, L1/2,0,0, upper); mk(box(L1*.5,.05,.37), matDark, L1*.45,.13,0, upper, false); mk(cylZ(.25,.62), matDark, L1,0,0, upper);
  const fore = new THREE.Group(); fore.position.set(L1,0,0); upper.add(fore);
  mk(box(L2,.22,.26), matOrange, L2/2,0,0, fore); mk(new THREE.SphereGeometry(.17,24,16), matDark, L2,0,0, fore);
  const hand = new THREE.Group(); hand.position.set(L2,0,0); fore.add(hand);
  mk(cyl(.09,.09,.2,20), matSteel, 0,-.1,0, hand); mk(box(.42,.06,.18), matDark, 0,-.22,0, hand);
  const fL = mk(box(.04,.24,.15), matRubber, -.17,-.34,0, hand), fR = mk(box(.04,.24,.15), matRubber, .17,-.34,0, hand);
  const led = mk(box(.12,.02,.19), EM(0x38bdf8,2), 0,-.185,0, hand, false);
  return {turret, upper, fore, hand, fL, fR, led, tip:new THREE.Vector3()};
})();
function setArm(yaw, r, y, closed){
  arm.turret.rotation.y = yaw;
  const dx = r, dy = y - H0, d = Math.min(Math.hypot(dx,dy), L1+L2-.02);
  const b = -Math.acos(Math.max(-1, Math.min(1, (d*d - L1*L1 - L2*L2)/(2*L1*L2))));
  const a = Math.atan2(dy,dx) - Math.atan2(L2*Math.sin(b), L1 + L2*Math.cos(b));
  arm.upper.rotation.z = a; arm.fore.rotation.z = b; arm.hand.rotation.z = -(a+b);
  const gap = closed ? .15 : .19; arm.fL.position.x = -gap; arm.fR.position.x = gap;
  arm.led.material.emissive.setHex(closed ? 0xfb923c : 0x38bdf8);
  arm.tip.set(r*Math.cos(yaw), y - GRIP, -r*Math.sin(yaw));
}

// ── 工件：形状 = 类型，外观 = 加工进度（毛坯 → 半成品 → 成品）──
const partGeo = {A:cyl(.15,.15,PART_H,28), B:box(.29,PART_H,.29), C:cyl(.18,.18,PART_H,6)};
const partMat = {};
function matsFor(type){ if(partMat[type]) return partMat[type]; const c = new THREE.Color(tcol(type));
  return partMat[type] = [
    SM(c.clone().lerp(new THREE.Color(0x5b6472), .42), {roughness:.95, metalness:.1}),
    SM(c, {roughness:.38, metalness:.7}),
    SM(c, {roughness:.16, metalness:.9, emissive:c, emissiveIntensity:.9}) ]; }
const STAGE_SC = [1.14, 1.0, .9];
const pmesh = parts.map(p => { const m = new THREE.Mesh(partGeo[p.type] || partGeo.B, matsFor(p.type)[0]);
  m.castShadow = true; m.receiveShadow = true; m.visible = false; scene.add(m); return m; });

// ═══ 按时间 t 求状态 ═════════════════════════════════════
function findMove(t){ let lo = 0, hi = moves.length-1, ans = -1;
  while(lo <= hi){ const m = (lo+hi)>>1; if(moves[m].t0 <= t){ ans = m; lo = m+1; } else hi = m-1; } return ans; }
function robotPose(t){
  const i = findMove(t);
  if(i < 0){ const A = anchor(D.start_pos); return {yaw:yawOf(A), e:0, tgt:A, closed:false, mi:-1}; }
  const m = moves[i], F = ptOf(m.from, m.fs), T = ptOf(m.to, m.ts);
  if(t >= m.t1) return {yaw:yawOf(T), e:0, tgt:T, closed:false, mi:-1};
  const P = i > 0 ? ptOf(moves[i-1].to, moves[i-1].ts) : anchor(D.start_pos);
  let u = t - m.t0; const [a,p,c,q] = m.ph;
  if(u < a) return {yaw:lerpA(yawOf(P), yawOf(F), ease(u/a)), e:0, tgt:F, closed:false, mi:i};
  u -= a; if(u < p){ const k = u/p; return {yaw:yawOf(F), e:Math.sin(Math.PI*k), tgt:F, closed:k >= .5, mi:i}; }
  u -= p; if(u < c) return {yaw:lerpA(yawOf(F), yawOf(T), ease(u/c)), e:0, tgt:T, closed:true, mi:i};
  u -= c; const k = q > 0 ? clamp01(u/q) : 1; return {yaw:yawOf(T), e:Math.sin(Math.PI*k), tgt:T, closed:k < .5, mi:i};
}
function partState(p, t){
  if(t < p.rel) return null;
  let last = -1; for(const k of p.mv){ if(moves[k].pk <= t) last = k; else break; }
  let stage = 0; for(const d of p.done) if(d <= t) stage++;
  if(last < 0) return {loc:'IN', slot:p.in_slot, stage};
  const m = moves[last]; if(t < m.pl) return {loc:'ROBOT', stage, m};
  return {loc:m.to, slot:m.ts, stage};
}
function machState(id, t, partOn){
  const ivs = D.miv[id] || []; let cur = null, down = null, prev = null;
  for(const v of ivs){ if(v.t0 > t) break; if(t < v.t1){ if(v.k === 'down') down = v; else cur = v; } else if(v.k !== 'down') prev = v; }
  if(down) return {s:'down'};
  if(cur) return {s:cur.k, iv:cur, f:(t-cur.t0)/Math.max(1,cur.t1-cur.t0)};
  if(partOn >= 0) return {s:'wait', ab:!!(prev && prev.p === partOn && prev.ab)};
  return {s:'idle'};
}

// ═══ 侧栏：订单面板 ══════════════════════════════════════
$('fam').textContent = (D.family ? D.family+' · ' : '') + (FAM[D.family] || D.sid); if(!D.family && !D.sid) $('fam').style.display = 'none';
$('lg-types').innerHTML = [...new Set(parts.map(p => p.type))].sort().map(t =>
  `<div><span class="sh ${t}" style="--c:${tcol(t)}"></span>${t} 型工件</div>`).join('');
const ordEls = D.orders.map(o => { const el = document.createElement('div'); el.className = 'ord';
  el.innerHTML = `<div class="h"><b>订单 ${o.id}</b><span></span></div>` + o.parts.map(i => { const p = parts[i];
    return `<div class="pc" style="--c:${tcol(p.type)}"><span class="sh ${p.type}"></span><span>${p.id}</span><span class="pips">${'<u></u>'.repeat(p.nops)}</span><small></small></div>`; }).join('');
  $('orders').appendChild(el);
  return {el, st:el.querySelector('.h span'), rows:[...el.querySelectorAll('.pc')].map(r => ({r, pips:[...r.querySelectorAll('u')], sm:r.querySelector('small'), sig:''})), sig:''}; });
const partRow = {}; D.orders.forEach((o,oi) => o.parts.forEach((pi,k) => partRow[pi] = ordEls[oi].rows[k]));

// ═══ 甘特时间轴 ══════════════════════════════════════════
const gantt = $('gantt'), gctx = gantt.getContext('2d'), gStatic = document.createElement('canvas');
const GX = 62, GR = 12, GTOP = 17, GBOT = 13; let gW = 0, gH = 0;
const gx = t => GX + (gW-GX-GR)*t/tEnd;
function hatch(x, x0, y0, w, h, col){ x.save(); x.beginPath(); x.rect(x0,y0,w,h); x.clip(); x.strokeStyle = col; x.lineWidth = 1.5;
  for(let i=-h;i<w;i+=6){ x.beginPath(); x.moveTo(x0+i,y0+h); x.lineTo(x0+i+h,y0); x.stroke(); } x.restore(); }
function buildGantt(){
  const dpr = Math.min(devicePixelRatio||1, 2); gW = gantt.clientWidth; gH = gantt.clientHeight; if(gW < 50 || gH < 30) return;
  gantt.width = gStatic.width = gW*dpr; gantt.height = gStatic.height = gH*dpr;
  const x = gStatic.getContext('2d'); x.setTransform(dpr,0,0,dpr,0,0); x.clearRect(0,0,gW,gH);
  const rows = [...D.machines, '机械臂'], rh = (gH-GTOP-GBOT)/rows.length;
  x.font = '11px sans-serif'; x.textBaseline = 'middle';
  const step = [10,20,30,60,120,300,600,1200].find(s => tEnd/1000/s <= 10) || 1800;
  for(let s=0; s*1000<=tEnd; s+=step){ const px = gx(s*1000); x.strokeStyle = 'rgba(148,163,184,.10)'; x.beginPath(); x.moveTo(px,GTOP); x.lineTo(px,gH-GBOT); x.stroke();
    x.fillStyle = '#64748b'; x.textAlign = 'center'; x.fillText(s+'s', px, gH-5); }
  rows.forEach((name,i) => { const y = GTOP + i*rh;
    x.fillStyle = 'rgba(148,163,184,.06)'; x.fillRect(GX, y+2, gW-GX-GR, rh-4);
    x.fillStyle = '#9fb0cc'; x.textAlign = 'right'; x.fillText(name, GX-8, y+rh/2);
    if(i < D.machines.length){ for(const v of (D.miv[name] || [])){ const x0 = gx(v.t0), w = Math.max(1.5, gx(v.t1)-x0);
        if(v.k === 'down'){ x.fillStyle = 'rgba(239,68,68,.28)'; x.fillRect(x0,y+2,w,rh-4); hatch(x,x0,y+2,w,rh-4,'rgba(239,68,68,.75)');
          if(w > 34){ x.fillStyle = '#fecaca'; x.textAlign = 'center'; x.fillText('停机', x0+w/2, y+rh/2); } }
        else if(v.k === 'setup'){ x.fillStyle = 'rgba(251,146,60,.30)'; x.fillRect(x0,y+3,w,rh-6); hatch(x,x0,y+3,w,rh-6,'rgba(251,146,60,.8)'); }
        else { const p = parts[v.p]; x.globalAlpha = v.ab ? .4 : .95; x.fillStyle = tcol(p.type); x.beginPath(); x.roundRect(x0,y+3,w,rh-6,3); x.fill(); x.globalAlpha = 1;
          if(w > 30){ x.fillStyle = '#0b1220'; x.textAlign = 'center'; x.font = 'bold 10px sans-serif'; x.fillText(p.id.replace(/^P0*/,'P'), x0+w/2, y+rh/2+.5); x.font = '11px sans-serif'; } } } }
    else for(const m of moves){ const x0 = gx(m.t0), w = Math.max(1.5, gx(m.t1)-x0); x.globalAlpha = .8; x.fillStyle = tcol(parts[m.p].type);
        x.beginPath(); x.roundRect(x0, y+rh*.28, w, rh*.44, 2); x.fill(); x.globalAlpha = 1; } });
  x.font = 'bold 10px sans-serif'; const used = [];
  const label = (txt, px, right, col) => { const w = x.measureText(txt).width, a = right ? px-5-w : px+6, b = a+w;
    if(a < GX-40 || b > gW || used.some(u => a < u[1]+6 && b > u[0]-6)) return; used.push([a,b]);
    x.fillStyle = col; x.textAlign = 'left'; x.fillText(txt, a, 7); };
  D.orders.forEach(o => { const px = gx(o.rel); x.fillStyle = '#fde68a'; x.beginPath(); x.moveTo(px-4,3); x.lineTo(px+4,3); x.lineTo(px,10); x.fill();
    label(o.id+' 到达', px, false, '#fde68a'); });
  D.orders.forEach(o => { if(o.done_t == null) return; const dx = gx(o.done_t);
    x.strokeStyle = 'rgba(110,231,183,.55)'; x.setLineDash([3,3]); x.beginPath(); x.moveTo(dx,2); x.lineTo(dx,gH-GBOT); x.stroke(); x.setLineDash([]);
    x.fillStyle = '#6ee7b7'; x.beginPath(); x.arc(dx,4,2.5,0,6.3); x.fill();
    label(o.id+' 交付 ✓', dx, true, '#6ee7b7'); });
}
function drawGantt(t){ if(!gW) return; const dpr = gantt.width/gW; gctx.setTransform(1,0,0,1,0,0); gctx.clearRect(0,0,gantt.width,gantt.height);
  gctx.drawImage(gStatic,0,0); gctx.setTransform(dpr,0,0,dpr,0,0); const px = gx(t);
  gctx.fillStyle = 'rgba(7,11,20,.55)'; gctx.fillRect(px, GTOP, gW-GR-px, gH-GTOP-GBOT);
  gctx.strokeStyle = '#fff'; gctx.lineWidth = 1.5; gctx.beginPath(); gctx.moveTo(px,GTOP-3); gctx.lineTo(px,gH-GBOT+2); gctx.stroke();
  gctx.fillStyle = '#fff'; gctx.beginPath(); gctx.arc(px,GTOP-3,3.5,0,6.3); gctx.fill(); }

// ═══ 播放控制 ════════════════════════════════════════════
let simT = 0, playing = false, speed = 50, lastT = 0, ended = false;
const evList = [];
(D.events || []).forEach(e => { if(e.kind === 'order_arrive') evList.push({t:e.t, c:'info', s:`📦 订单 ${e.order} 到达（${e.n_parts} 个工件）`});
  else if(e.kind === 'machine_down') evList.push({t:e.t, c:'err', s:`⚠ ${e.machine} 突发停机，在制工序作废`});
  else if(e.kind === 'machine_recover') evList.push({t:e.t, c:'ok', s:`🔧 ${e.machine} 恢复运行`}); });
D.orders.forEach(o => { if(o.done_t != null) evList.push({t:o.done_t, c:'ok', s:`✅ 订单 ${o.id} 交付，用时 ${sec(o.done_t-o.rel)} s`}); });
evList.sort((a,b) => a.t-b.t);
function toast(e){ const el = document.createElement('div'); el.className = 'toast '+e.c; el.textContent = e.s; $('toasts').appendChild(el);
  while($('toasts').children.length > 3) $('toasts').firstChild.remove();
  setTimeout(() => el.style.opacity = 0, 2600); setTimeout(() => el.remove(), 3200); }
function setPlaying(v){ playing = v; $('play').textContent = v ? '⏸ 暂停' : '▶ 播放'; }
function seek(t){ simT = Math.max(0, Math.min(tEnd, t)); lastT = simT; ended = false; $('end').style.display = 'none'; $('toasts').innerHTML = ''; }
function finish(){ ended = true; setPlaying(false); const all = D.orders_done >= D.orders_total && D.valid;
  $('end-h').textContent = all ? `✅ 全部 ${D.orders_total} 个订单已交付` : `⚠ 仅交付 ${D.orders_done}/${D.orders_total} 个订单`;
  $('end-v').innerHTML = D.mean_flow != null ? (D.mean_flow/1000).toFixed(1)+'<small>s</small>' : '—';
  $('end-p').textContent = '订单平均流程时间（越短越好）'; $('end').style.display = 'block'; }
$('play').onclick = () => { if(ended || simT >= tEnd) seek(0); setPlaying(!playing); };
$('reset').onclick = $('end-btn').onclick = () => { seek(0); setPlaying(true); };
$('speed').onchange = e => speed = +e.target.value;
$('intro-x').onclick = () => { const m = $('intro').classList.toggle('min'); $('intro-x').textContent = m ? '+' : '–'; };
addEventListener('keydown', e => { if(e.code === 'Space' && e.target.tagName !== 'SELECT'){ e.preventDefault(); $('play').click(); } });
let dragging = false; const gSeek = e => { const r = gantt.getBoundingClientRect(); seek((e.clientX-r.left-GX)/(gW-GX-GR)*tEnd); };
gantt.addEventListener('pointerdown', e => { dragging = true; gantt.setPointerCapture(e.pointerId); gSeek(e); });
gantt.addEventListener('pointermove', e => dragging && gSeek(e));
gantt.addEventListener('pointerup', () => dragging = false);

// ═══ 每帧更新 ════════════════════════════════════════════
const ST_TXT = {idle:'空闲'}, _v = new THREE.Vector3(), now0 = performance.now();
function setText(o, txt){ if(o.txt !== txt){ o.txt = txt; o.lb.el.querySelector('.st').textContent = txt; } }
function render(t, dt, now){
  // 机械臂
  const ps = robotPose(t), R = Math.hypot(ps.tgt.x, ps.tgt.z);
  setArm(ps.yaw, lerp(TUCK_R, R, ps.e), lerp(TUCK_Y, ps.tgt.y + PART_H + GRIP, ps.e), ps.closed);
  // 工件
  const on = {}, cnt = {IN:0, BUF:0, OUT:0}; let carrying = -1;
  parts.forEach((p,i) => { const m = pmesh[i], s = partState(p,t), row = partRow[i]; let sig, loc = '';
    if(!s){ m.visible = false; sig = 'n'; }
    else { m.visible = true; const mats = matsFor(p.type), st = Math.min(2, s.stage >= p.nops ? 2 : s.stage);
      if(m.material !== mats[st]) m.material = mats[st];
      let sc = STAGE_SC[st];
      if(s.loc === 'ROBOT'){ m.position.set(arm.tip.x, arm.tip.y - PART_H/2, arm.tip.z); m.rotation.y = ps.yaw; carrying = i; }
      else { const q = ptOf(s.loc, s.slot); m.position.set(q.x, q.y + PART_H/2, q.z);
        if(machines[s.loc]) on[s.loc] = i; else cnt[s.loc]++;
        if(s.loc === 'IN') sc *= ease((t-p.rel)/1500 + .15); }
      m.scale.set(sc, 1, sc); loc = s.loc; sig = s.loc+s.stage; }
    if(row){ let cur = -1, txt = '未到达';
      if(s){ const ms = machines[s.loc] ? machState(s.loc, t, i) : null;
        if(ms && ms.s === 'proc' && ms.iv.p === i){ cur = ms.iv.op; txt = `${s.loc} 加工中`; }
        else if(ms && ms.s === 'setup'){ txt = `${s.loc} 换型中`; } else if(ms && ms.s === 'down'){ txt = `${s.loc} 停机`; }
        else if(ms){ txt = `${s.loc} 待取件`; }
        else txt = {IN:'原料区', BUF:'缓冲区暂存', OUT:'已出货 ✓', ROBOT:'搬运中'}[s.loc] || s.loc;
        sig += '|'+cur+txt; }
      if(row.sig !== sig){ row.sig = sig; row.r.dataset.l = loc; row.sm.textContent = txt;
        row.pips.forEach((u,k) => u.className = s && k < s.stage ? 'on' : (k === cur ? 'cur' : '')); } } });
  // 机床
  const blink = .5 + .5*Math.sin(now*.009);
  for(const id of D.machines){ const M = machines[id], partOn = on[id] ?? -1, ms = machState(id, t, partOn), s = ms.s;
    const col = ST_COL[s]; M.bar.material.emissive.setHex(col);
    M.bar.material.emissiveIntensity = s === 'down' ? .6+3*blink : s === 'wait' ? 1+1.6*blink : s === 'idle' ? .5 : 2.4;
    M.tower[0].material.emissiveIntensity = s === 'down' ? .3+3.5*blink : .05;
    M.tower[1].material.emissiveIntensity = (s === 'setup' || s === 'wait') ? 2.6 : .05;
    M.tower[2].material.emissiveIntensity = s === 'proc' ? 2.8 : .05;
    M.lamp.color.setHex(s === 'down' ? 0xff3b3b : s === 'setup' ? 0xffa657 : 0xfff1d6);
    M.lamp.intensity = s === 'proc' ? 7 : s === 'setup' ? 4 : s === 'down' ? 1+4*blink : s === 'wait' ? 2.5 : 1;
    M.scr.material.emissive.setHex(s === 'down' ? 0xef4444 : 0x38bdf8);
    M.ring.visible = s === 'down'; if(s === 'down'){ M.ring.material.opacity = .25+.6*blink; const k = 1+.05*blink; M.ring.scale.set(k,k,1); }
    const tgt = (s === 'proc' || s === 'setup' || s === 'down') ? 0 : 1; M.door += (tgt - M.door)*Math.min(1, dt*9);
    M.doorL.position.x = -.38 - .64*M.door; M.doorR.position.x = .38 + .64*M.door;
    M.head.position.y += ((s === 'proc' ? -.16 : .12) - M.head.position.y)*Math.min(1, dt*7);
    if(s === 'proc') M.tool.rotation.y += dt*55; else if(s === 'setup') M.tool.rotation.y += dt*6;
    M.sparks.visible = s === 'proc';
    if(s === 'proc'){ const a = M.sparks.geometry.attributes.position; M.seed.forEach((sd,k) => { const ph = (now*.0016*sd[3] + sd[2]) % 1, r = ph*sd[1]*.55;
        a.setXYZ(k, Math.cos(sd[0])*r, .05 + ph*.5*sd[1] - ph*ph*.85, Math.sin(sd[0])*r); }); a.needsUpdate = true; }
    let txt = '空闲';
    if(s === 'proc') txt = `加工中 · ${parts[ms.iv.p].id} 工序 ${ms.iv.op+1}`; else if(s === 'setup') txt = `换型中 · 准备加工 ${parts[ms.iv.p].type} 型`;
    else if(s === 'down') txt = '⚠ 故障停机'; else if(s === 'wait') txt = ms.ab ? '工序作废 · 待取走重做' : '已完成 · 等待取件';
    setText(M, txt); if(M.state !== s){ M.state = s; M.lb.el.dataset.s = s; }
    if(ms.f != null) M.lb.el.querySelector('.pb i').style.width = (clamp01(ms.f)*100).toFixed(1)+'%'; }
  setText(tables.IN, cnt.IN+' 件待加工'); setText(tables.BUF, `${cnt.BUF}/${D.buf_cap}`); setText(tables.OUT, `${cnt.OUT}/${parts.length} 件`);
  // 字幕
  let cap;
  if(ps.mi >= 0){ const m = moves[ps.mi], p = parts[m.p], st = partState(p,t)?.stage ?? 0;
    const why = machines[m.to] ? `做工序 ${Math.min(st+1,p.nops)}` : m.to === 'OUT' ? '完工出货' : '暂存等待';
    cap = `🤖 机械臂搬运 <b style="color:${tcol(p.type)}">${p.id}（${p.type} 型）</b>：${locName(m.from)} → <b>${locName(m.to)}</b> <span class="dim">· ${why}</span>`; }
  else cap = t >= tEnd ? '🏁 仿真结束' : '🤖 机械臂空闲 <span class="dim">· 等待机床完工或新订单</span>';
  if(render.cap !== cap){ render.cap = cap; $('caption').innerHTML = cap; }
  // 订单与指标
  let nd = 0, fsum = 0;
  D.orders.forEach((o,i) => { const E = ordEls[i]; let s, txt;
    if(t < o.rel){ s = 'pending'; txt = `${sec(o.rel)} s 时到达`; }
    else if(o.done_t != null && t >= o.done_t){ s = 'done'; txt = `已交付 · 用时 ${sec(o.done_t-o.rel)} s`; nd++; fsum += o.done_t-o.rel; }
    else { s = 'active'; txt = `进行中 · 已等待 ${sec(t-o.rel)} s`; }
    if(E.sig !== s+txt){ E.sig = s+txt; E.el.dataset.s = s; E.st.textContent = txt; } });
  $('k-t').innerHTML = sec(t)+'<small>s</small>'; $('k-o').textContent = nd+'/'+D.orders.length;
  $('k-f').innerHTML = nd ? (fsum/nd/1000).toFixed(1)+'<small>s</small>' : '—';
  $('time').textContent = `${sec(t)} s / ${sec(tEnd)} s`;
  drawGantt(t);
}
function layoutLabels(){ const w = stage.clientWidth, h = stage.clientHeight;
  for(const l of labels){ _v.copy(l.anchor).project(camera); const vis = _v.z < 1;
    l.el.style.display = vis ? '' : 'none'; if(vis) l.el.style.transform = `translate(-50%,-100%) translate(${((_v.x*.5+.5)*w).toFixed(1)}px,${((-_v.y*.5+.5)*h).toFixed(1)}px)`; } }
function resize(){ const w = stage.clientWidth, h = stage.clientHeight; if(!w || !h) return;
  renderer.setSize(w,h); composer.setSize(w,h); camera.aspect = w/h; camera.updateProjectionMatrix(); buildGantt(); }
addEventListener('resize', resize); resize();
let prev = performance.now();
function frame(now){ requestAnimationFrame(frame); const dt = Math.max(0, Math.min(.1, (now-prev)/1000)); prev = now;
  if(playing){ simT += dt*1000*speed;
    for(const e of evList) if(e.t > lastT && e.t <= simT) toast(e);
    if(simT >= tEnd){ simT = tEnd; finish(); } lastT = simT; }
  if(!userCam){ const az = Math.sin((now-now0)*.00016)*.26, pol = .93, rad = 15.2*Math.max(1, 1.55/camera.aspect);
    camera.position.set(rad*Math.sin(pol)*Math.sin(az), 1.0+rad*Math.cos(pol), rad*Math.sin(pol)*Math.cos(az)); }
  controls.update(); render(simT, dt, now); layoutLabels(); composer.render(); }
$('load')?.remove(); window.__vizReady = true;
requestAnimationFrame(frame);
const hashT = /[#&]t=([\d.]+)/.exec(location.hash);
if(hashT){ seek(+hashT[1]*1000); if(/play/.test(location.hash)) setPlaying(true); } else setTimeout(() => { if(!playing && simT === 0) setPlaying(true); }, 900);
window.__viz = {seek, setPlaying, get t(){ return simT; }, tEnd};
</script>
<script>
// Three.js 从 CDN 加载失败时给出明确提示，而不是一直显示“加载中”
setTimeout(function(){ if(!window.__vizReady){ var el = document.getElementById('load');
  if(el) el.textContent = '3D 引擎加载失败：请检查网络能否访问 cdn.jsdelivr.net，然后刷新页面'; } }, 12000);
window.addEventListener('error', function(e){ if(!window.__vizReady){ var el = document.getElementById('load'); if(el) el.textContent = '3D 场景初始化失败：' + (e.message || ''); } });
</script></body></html>
"""


if __name__ == "__main__":
    import sys, os, argparse
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="dev-F6-301")
    ap.add_argument("--policy",   default="example_policy.py")
    ap.add_argument("--fn",       default="decide")
    ap.add_argument("--out",      default="out_3d.html")
    args = ap.parse_args()

    from simkit.scenarios import INSTANCE_BY_ID
    from simkit.engine import Engine
    import importlib.util
    sc = INSTANCE_BY_ID[args.instance]
    spec = importlib.util.spec_from_file_location("pol", args.policy)
    mod  = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    fn   = getattr(mod, args.fn)
    eng  = Engine(sc); gen = eng.run()
    try:
        obs = next(gen)
        while True: obs = gen.send(fn(obs))
    except StopIteration as e: result = e.value
    html = build_3d_html(sc, result)
    pathlib.Path(args.out).write_text(html, encoding="utf-8")
    print(f"[viz3d] 写出 {args.out} ({len(html)//1024}KB)")
