"""
3D 仿真可视化（Three.js 自包含 HTML，需联网加载 CDN）
用法：
    from simkit.visualize_3d import build_3d_html
    html = build_3d_html(sc, result)
    pathlib.Path("out_3d.html").write_text(html)
CLI:
    python3 simkit/visualize_3d.py --instance dev-F6-301 --policy example_policy.py --out out_3d.html
"""
import json, pathlib

# 机台/缓冲位的 XZ 坐标（Three.js: X=右 Z=前 Y=上）
_POS_XZ: dict[str, tuple[float,float]] = {
    "M1": ( 3.6, -1.5),
    "M2": ( 3.6,  2.0),
    "M3": ( 0.0,  3.8),
    "M4": (-3.6,  2.0),
    "M5": (-3.6, -1.5),
    "M6": ( 0.0, -3.8),
    "IN":  ( 1.2, -5.4),
    "OUT": (-1.2, -5.4),
    "BUF": ( 5.8,  0.3),
}


def build_3d_html(sc: dict, result: dict) -> str:
    machines = [m["id"] for m in sc.get("machines", [])]
    all_locs = set(machines) | {"IN", "OUT", "BUF"}
    positions = {k: list(_POS_XZ[k]) for k in all_locs if k in _POS_XZ}

    # 收集所有零件及其类型
    parts: dict[str, str] = {}
    for iv in result.get("intervals", []):
        pid = iv.get("part")
        if pid and pid not in parts:
            parts[pid] = iv.get("type", "A")

    data = {
        "machines": machines,
        "positions": positions,
        "parts": parts,
        "intervals": result.get("intervals", []),
        "events":    result.get("events", []),
        "t_end_ms":       result.get("t_end_ms", 1_800_000),
        "mean_flow_ms":   result.get("mean_flow_ms"),
        "orders_done":    result.get("orders_done", 0),
        "orders_total":   result.get("orders_total", 0),
        "scenario_id":    sc.get("id", ""),
    }
    return _HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3D 调度可视化</title>
<style>
:root{--bg:#0b1220;--card:#13203a;--text:#e6ecf5;--muted:#8ea3c4;--accent:#4aa3ff;--ok:#34d399;--err:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Segoe UI",sans-serif;background:var(--bg);color:var(--text);overflow:hidden;height:100vh}
#app{display:flex;height:100vh}
#scene{flex:1;position:relative}
#hud{position:absolute;top:12px;left:12px;background:rgba(13,24,42,.86);
  padding:12px 16px;border-radius:10px;backdrop-filter:blur(8px);border:1px solid rgba(74,163,255,.25);min-width:220px}
#hud h2{font-size:14px;font-weight:700;margin-bottom:6px;color:var(--accent)}
#hud .row{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin:2px 0}
#hud .row span:last-child{color:var(--text);font-variant-numeric:tabular-nums}
#hud .legend{margin-top:8px;display:flex;gap:8px;flex-wrap:wrap}
.legend-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:middle}
#controls{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
  background:rgba(13,24,42,.86);padding:8px 16px;border-radius:24px;border:1px solid rgba(74,163,255,.25);
  display:flex;align-items:center;gap:14px;backdrop-filter:blur(8px)}
#controls button{background:transparent;color:var(--text);border:1px solid rgba(255,255,255,.15);
  border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px}
#controls button:hover{background:rgba(74,163,255,.15);border-color:var(--accent)}
#controls input[type=range]{width:180px;accent-color:var(--accent)}
#speedSel{background:transparent;color:var(--text);border:1px solid rgba(255,255,255,.15);border-radius:6px;padding:4px 6px}
#timeLabel{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;min-width:90px}
#legend{position:absolute;top:12px;right:12px;background:rgba(13,24,42,.86);padding:10px 14px;
  border-radius:10px;border:1px solid rgba(74,163,255,.25);font-size:12px;color:var(--muted);backdrop-filter:blur(8px)}
#legend div{margin:3px 0}
canvas{display:block}
.loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:14px}
</style></head><body>
<div id="app"><div id="scene">
<div class="loading" id="load">加载中…</div>
<div id="hud"><h2>🏭 单臂柔性机加单元</h2>
<div class="row"><span>场景</span><span id="sc-id">—</span></div>
<div class="row"><span>仿真时刻</span><span id="t-now">0 s</span></div>
<div class="row"><span>平均流程时间</span><span id="m-flow">—</span></div>
<div class="row"><span>订单完成</span><span id="o-done">—</span></div>
<div class="legend" id="part-legend"></div></div>
<div id="legend">
<div><span class="legend-dot" style="background:#4aa3ff"></span>加工</div>
<div><span class="legend-dot" style="background:#f5b942"></span>换型</div>
<div><span class="legend-dot" style="background:#f87171"></span>停机</div>
<div><span class="legend-dot" style="background:#94a3b8"></span>移动</div></div>
<div id="controls">
<button id="play">▶ 播放</button>
<button id="reset">⟲ 重置</button>
<span style="color:var(--muted);font-size:12px">速度</span>
<select id="speedSel"><option value="1">1×</option><option value="5">5×</option>
<option value="20" selected>20×</option><option value="100">100×</option></select>
<input type="range" id="seek" min="0" max="1000" value="0">
<span id="timeLabel">0.0 s / 0.0 s</span></div></div></div>
<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/examples/js/controls/OrbitControls.js"></script>
<script>
const DATA = __DATA_JSON__;
</script>
<script>
// ─── Three.js 场景搭建 ───────────────────────────────────
let scene,camera,renderer,controls,clock;
let machineMeshes={},partMeshes={},robotMesh,baseGroup;
let trackLine;
const POS = DATA.positions;
const TYPE_COLOR = {A:0x4aa3ff,B:0xf5b942,C:0x34d399,D:0xc084fc,E:0xf472b6,F:0x60a5fa};
const KIND_COLOR = {proc:0x4aa3ff,setup:0xf5b942,down:0xf87171,move:0x94a3b8};
function init(){
  scene=new THREE.Scene();scene.background=new THREE.Color(0x0b1220);scene.fog=new THREE.Fog(0x0b1220,18,40);
  camera=new THREE.PerspectiveCamera(50,innerWidth/innerHeight,0.1,100);
  camera.position.set(9,9,12);camera.lookAt(0,0,0);
  renderer=new THREE.WebGLRenderer({antialias:true});
  renderer.setSize(innerWidth-360,innerHeight);renderer.shadowMap.enabled=true;
  document.getElementById('scene').appendChild(renderer.domElement);
  controls=new THREE.OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true;controls.target.set(0,0.5,0);
  // 光照
  scene.add(new THREE.AmbientLight(0x404a6b,0.6));
  const d=new THREE.DirectionalLight(0xffffff,0.9);d.position.set(8,14,6);
  d.castShadow=true;d.shadow.mapSize.set(2048,2048);
  d.shadow.camera.left=-12;d.shadow.camera.right=12;d.shadow.camera.top=12;d.shadow.camera.bottom=-12;
  scene.add(d);scene.add(new THREE.HemisphereLight(0x4a6fa5,0x0b1220,0.3));
  // 地面
  const floor=new THREE.Mesh(new THREE.CircleGeometry(14,64),
    new THREE.MeshStandardMaterial({color:0x0e1830,roughness:0.9,metalness:0.1}));
  floor.rotation.x=-Math.PI/2;floor.receiveShadow=true;scene.add(floor);
  // 网格线
  const grid=new THREE.GridHelper(20,20,0x1a2a4a,0x13203a);grid.position.y=0.01;scene.add(grid);
  // 机台
  DATA.machines.forEach(mid=>{
    const p=POS[mid];if(!p)return;
    const g=new THREE.Group();
    const base=new THREE.Mesh(new THREE.BoxGeometry(1.6,0.5,1.6),
      new THREE.MeshStandardMaterial({color:0x1e3a5f,roughness:0.4,metalness:0.6}));
    base.position.y=0.25;base.castShadow=true;base.receiveShadow=true;g.add(base);
    const top=new THREE.Mesh(new THREE.BoxGeometry(1.4,0.3,1.4),
      new THREE.MeshStandardMaterial({color:0x2a4a7a,roughness:0.3,metalness:0.7}));
    top.position.y=0.65;top.castShadow=true;g.add(top);
    // 指示灯
    const lamp=new THREE.Mesh(new THREE.SphereGeometry(0.08,8,8),
      new THREE.MeshBasicMaterial({color:0x4aa3ff}));
    lamp.position.set(0.6,0.85,0.6);g.add(lamp);g.userData.lamp=lamp;
    // 标签
    const lbl=makeLabel(mid);lbl.position.set(0,1.5,0);g.add(lbl);
    g.position.set(p[0],0,p[1]);scene.add(g);machineMeshes[mid]=g;
  });
  // IN / OUT / BUF
  [['IN',0x34d399],['OUT',0xf87171],['BUF',0x8ea3c4]].forEach(([id,col])=>{
    const p=POS[id];if(!p)return;
    const g=new THREE.Group();
    const pad=new THREE.Mesh(new THREE.CylinderGeometry(0.9,0.9,0.15,32),
      new THREE.MeshStandardMaterial({color:col,transparent:true,opacity:0.6,emissive:col,emissiveIntensity:0.3}));
    pad.position.y=0.075;pad.receiveShadow=true;g.add(pad);
    const lbl=makeLabel(id);lbl.position.set(0,1.2,0);g.add(lbl);
    g.position.set(p[0],0,p[1]);scene.add(g);machineMeshes[id]=g;
  });
  // 轨道
  buildTrack();
  // 机器人
  robotMesh=new THREE.Group();
  const body=new THREE.Mesh(new THREE.CylinderGeometry(0.22,0.28,0.6,16),
    new THREE.MeshStandardMaterial({color:0xe6ecf5,metalness:0.8,roughness:0.2}));
  body.position.y=0.3;body.castShadow=true;robotMesh.add(body);
  const arm=new THREE.Mesh(new THREE.BoxGeometry(0.12,0.5,0.12),
    new THREE.MeshStandardMaterial({color:0x94a3b8,metalness:0.7}));
  arm.position.y=0.9;arm.castShadow=true;robotMesh.add(arm);
  const grip=new THREE.Mesh(new THREE.BoxGeometry(0.25,0.08,0.25),
    new THREE.MeshStandardMaterial({color:0x4aa3ff,emissive:0x4aa3ff,emissiveIntensity:0.4}));
  grip.position.y=0.7;robotMesh.add(grip);robotMesh.userData.grip=grip;
  robotMesh.position.set(POS['IN'][0],0,POS['IN'][1]);
  robotMesh.visible=false;scene.add(robotMesh);
  // 零件
  Object.entries(DATA.parts).forEach(([pid,type])=>{
    const col=TYPE_COLOR[type]||0x8ea3c4;
    const m=new THREE.Mesh(new THREE.BoxGeometry(0.35,0.35,0.35),
      new THREE.MeshStandardMaterial({color:col,emissive:col,emissiveIntensity:0.25,roughness:0.4}));
    m.castShadow=true;m.visible=false;m.userData={pid,type};partMeshes[pid]=m;scene.add(m);
  });
  // HUD
  document.getElementById('sc-id').textContent=DATA.scenario_id||'—';
  document.getElementById('m-flow').textContent=DATA.mean_flow_ms?(DATA.mean_flow_ms/1000).toFixed(1)+' s':'—';
  document.getElementById('o-done').textContent=DATA.orders_done+'/'+DATA.orders_total;
  const lg=document.getElementById('part-legend');
  const types=[...new Set(Object.values(DATA.parts))];
  lg.innerHTML=types.map(t=>`<span><span class="legend-dot" style="background:#${(TYPE_COLOR[t]||0x8ea3c4).toString(16).padStart(6,'0')}"></span>${t}</span>`).join('');
  document.getElementById('load').style.display='none';
}
function makeLabel(txt){
  const c=document.createElement('canvas');c.width=128;c.height=64;
  const ctx=c.getContext('2d');ctx.fillStyle='rgba(13,24,42,0.85)';
  ctx.fillRect(0,0,128,64);ctx.strokeStyle='#4aa3ff';ctx.lineWidth=3;
  ctx.strokeRect(2,2,124,60);ctx.fillStyle='#e6ecf5';
  ctx.font='bold 36px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillText(txt,64,32);
  const tex=new THREE.CanvasTexture(c);tex.minFilter=THREE.LinearFilter;
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,depthTest:false}));
  sp.scale.set(1.2,0.6,1);return sp;
}
function buildTrack(){
  // 简化的机器人移动轨道（IN→各机台→OUT 的中心环）
  const pts=[POS['IN'],POS['M5'],POS['M1'],POS['BUF'],POS['M2'],POS['M4'],POS['M3'],POS['M6'],POS['OUT']]
    .filter(Boolean).map(p=>new THREE.Vector3(p[0],0.02,p[1]));
  if(pts.length<2){
    const geom=new THREE.BufferGeometry().setFromPoints(pts);
    trackLine=new THREE.Line(geom,new THREE.LineBasicMaterial({color:0x1a3a6a}));
  }else{
    const curve=new THREE.CatmullRomCurve3(pts,true,'catmullrom',0.3);
    const cp=curve.getPoints(80);
    const geom=new THREE.BufferGeometry().setFromPoints(cp);
    trackLine=new THREE.Line(geom,new THREE.LineBasicMaterial({color:0x1a3a6a,transparent:true,opacity:0.5}));
  }
  scene.add(trackLine);
}
// ─── 时间轴播放 ───────────────────────────────────────────
let tEnd=DATA.t_end_ms||1800000;
let allEvents=[];
DATA.intervals.forEach(iv=>{
  allEvents.push({t:iv.t_start,type:'start',iv});
  allEvents.push({t:iv.t_end,  type:'end',  iv});
});
DATA.events.forEach(ev=>allEvents.push({t:ev.t,type:'event',ev}));
allEvents.sort((a,b)=>a.t-b.t);
let cursor=0;let playing=false;let speed=20;let simT=0;
// 每个机台当前激活的 interval（高亮）
const activeIv={}; // mid -> iv
function lerp(a,b,t){return a+(b-a)*t}
function posOf(loc){const p=POS[loc];return p?[p[0],0,p[1]]:null}
function moveTo(mesh,x,z,dur){
  mesh.userData.tx=x;mesh.userData.tz=z;mesh.userData.moving=true;
}
function update(dtMs){
  if(!playing)return;
  simT+=dtMs*speed;
  if(simT>=tEnd){simT=tEnd;playing=false;document.getElementById('play').textContent='▶ 播放';}
  // 推进事件
  while(cursor<allEvents.length&&allEvents[cursor].t<=simT){
    const e=allEvents[cursor++];
    if(e.type==='start'){
      const iv=e.iv;const r=iv.resource;
      activeIv[r]=iv;
      if(r!=='ROBOT'&&machineMeshes[r]&&machineMeshes[r].userData.lamp){
        machineMeshes[r].userData.lamp.material.color.setHex(KIND_COLOR[iv.kind]||0x4aa3ff);
      }
      if(iv.kind==='move'){
        // 显示机器人移动
        robotMesh.visible=true;
        const from=posOf(iv.from),to=posOf(iv.to);
        if(from&&to){robotMesh.userData.fx=from[0];robotMesh.userData.fz=from[2];
          robotMesh.userData.tx=to[0];robotMesh.userData.tz=to[2];
          robotMesh.userData.mt0=iv.t_start;robotMesh.userData.mt1=iv.t_end;
          // 零件跟随
          const pm=partMeshes[iv.part];if(pm){pm.visible=true;
            pm.userData.onRobot=true;}
        }
      }
    }else if(e.type==='end'){
      const iv=e.iv;const r=iv.resource;
      if(activeIv[r]===iv)delete activeIv[r];
      if(r!=='ROBOT'&&machineMeshes[r]&&machineMeshes[r].userData.lamp){
        machineMeshes[r].userData.lamp.material.color.setHex(0x4aa3ff);
      }
      if(iv.kind==='proc'||iv.kind==='setup'){
        const pm=partMeshes[iv.part];
        if(pm){pm.visible=false;pm.userData.onRobot=false;}
      }
    }
  }
  // 插值机器人位置
  if(robotMesh.visible&&robotMesh.userData.mt1){
    const f=(simT-robotMesh.userData.mt0)/(robotMesh.userData.mt1-robotMesh.userData.mt0);
    if(f>=0&&f<=1){
      robotMesh.position.x=lerp(robotMesh.userData.fx,robotMesh.userData.tx,f);
      robotMesh.position.z=lerp(robotMesh.userData.fz,robotMesh.userData.tz,f);
    }
    if(f>=1){robotMesh.userData.mt1=null;}
  }
  // 零件跟随机器人或停在机台
  Object.entries(partMeshes).forEach(([pid,m])=>{
    if(!m.visible)return;
    if(m.userData.onRobot){
      m.position.set(robotMesh.position.x,0.8,robotMesh.position.z);
    }else{
      // 找当前在哪个机台加工
      for(const r in activeIv){
        const iv=activeIv[r];
        if(iv.part===pid&&iv.kind!=='move'&&POS[r]){
          m.position.set(POS[r][0],0.9,POS[r][1]);break;
        }
      }
    }
  });
  // HUD 更新
  document.getElementById('t-now').textContent=(simT/1000).toFixed(1)+' s';
  document.getElementById('timeLabel').textContent=(simT/1000).toFixed(1)+' s / '+(tEnd/1000).toFixed(1)+' s';
  document.getElementById('seek').value=Math.round(simT/tEnd*1000);
}
function animate(){
  requestAnimationFrame(animate);
  const dt=clock.getDelta()*1000;
  update(dt);
  controls.update();renderer.render(scene,camera);
}
function onResize(){camera.aspect=(innerWidth-360)/innerHeight;camera.updateProjectionMatrix();
  renderer.setSize(innerWidth-360,innerHeight);}
// ─── 控制 ─────────────────────────────────────────────────
window.addEventListener('resize',onResize);
document.getElementById('play').onclick=()=>{playing=!playing;
  document.getElementById('play').textContent=playing?'⏸ 暂停':'▶ 播放';};
document.getElementById('reset').onclick=()=>{simT=0;cursor=0;playing=false;
  Object.values(machineMeshes).forEach(m=>{if(m.userData.lamp)m.userData.lamp.material.color.setHex(0x4aa3ff);});
  Object.values(partMeshes).forEach(m=>{m.visible=false;m.userData.onRobot=false;});
  robotMesh.visible=false;document.getElementById('play').textContent='▶ 播放';
  document.getElementById('t-now').textContent='0 s';};
document.getElementById('speedSel').onchange=e=>speed=+e.target.value;
document.getElementById('seek').oninput=e=>{simT=+e.target.value/1000*tEnd;cursor=0;
  Object.values(partMeshes).forEach(m=>m.visible=false);robotMesh.visible=false;
  // 快进状态重建
  for(let i=0;i<allEvents.length;i++){if(allEvents[i].t>simT){cursor=i;break;}}
  // 简化：不重建历史高亮，仅移动时间指针
};
clock=new THREE.Clock();
init();animate();
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
