"""OJ HTTP 服务器 v2 — 注册/登录 · 代码编辑器提交 · 实时状态页"""
import sys, os, time, pathlib, collections, secrets
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from flask import Flask, request, jsonify, abort, Response, send_file
from oj.db import (init_db, enqueue, get_submission, get_leaderboard,
                   create_team, get_conn, get_all_teams, delete_team,
                   get_all_submissions)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # 256 KB 上传上限

UPLOAD_DIR = pathlib.Path(__file__).parent / "uploads"
VIZ_DIR    = pathlib.Path(__file__).parent / "viz"
UPLOAD_DIR.mkdir(exist_ok=True)
VIZ_DIR.mkdir(exist_ok=True)

CURRENT_PHASE = os.environ.get("OJ_PHASE", "A")
ADMIN_SECRET  = os.environ.get("OJ_ADMIN", "hackerson-admin")

MAX_CODE_BYTES  = 64 * 1024   # 64 KB
MAX_LABEL_CHARS = 128

# 注册 IP 简单限速：每 IP 每分钟最多 3 次
_reg_counter: dict[str, list] = collections.defaultdict(list)

def _reg_rate_ok(ip: str) -> bool:
    now = time.time()
    _reg_counter[ip] = [t for t in _reg_counter[ip] if now - t < 60]
    if len(_reg_counter[ip]) >= 3:
        return False
    _reg_counter[ip].append(now)
    return True

# ── 认证 ─────────────────────────────────────────────────
def _auth():
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "): return None
    token = hdr[7:].strip()
    conn = get_conn()
    row = conn.execute("SELECT * FROM teams WHERE token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        team = _auth()
        if not team:
            return jsonify({"error": "认证失败，请提供有效的 Bearer token"}), 401
        request.team = team
        return f(*a, **kw)
    return wrapper

# ── 注册 ──────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    ip = request.remote_addr or "unknown"
    if not _reg_rate_ok(ip):
        return jsonify({"error": "注册过于频繁，请稍后再试"}), 429
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 40:
        return jsonify({"error": "队伍名称须为 1–40 个字符"}), 400
    # 只允许字母、数字、中文、空格、连字符
    import re
    if not re.match(r'^[\w一-鿿 \-]+$', name):
        return jsonify({"error": "队伍名称含非法字符"}), 400
    conn = get_conn()
    existing = conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()
    conn.close()
    if existing:
        return jsonify({"error": "队伍名已存在，请联系组委会获取 token"}), 409
    t = create_team(name)
    return jsonify(t), 201

# ── 登录（队伍 token 或管理员密钥）────────────────────────
_ADMIN_PLACEHOLDERS = {"", "hackerson-admin", "请改成随机字符串"}

def _is_admin_token(tok: str) -> bool:
    """管理员密钥未设置或仍为占位值时，禁用管理员登录。"""
    if ADMIN_SECRET in _ADMIN_PLACEHOLDERS or len(ADMIN_SECRET) < 12:
        return False
    return bool(tok) and secrets.compare_digest(tok, ADMIN_SECRET)

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "请提供 token"}), 400
    if _is_admin_token(token):
        return jsonify({"role": "admin", "name": "管理员"})
    conn = get_conn()
    row = conn.execute("SELECT id, name FROM teams WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "token 无效"}), 401
    return jsonify({"role": "team", **dict(row)})

# ── 文件提交 ───────────────────────────────────────────────
@app.route("/api/submit", methods=["POST"])
@require_auth
def submit():
    team = request.team
    phase = CURRENT_PHASE
    code = label = None
    # 支持两种方式：multipart 文件 或 JSON 代码字符串
    if request.files.get("policy"):
        f = request.files["policy"]
        if not f.filename.lower().endswith(".py"):
            return jsonify({"error": "只接受 .py 策略文件"}), 400
        raw = f.read(MAX_CODE_BYTES + 1)
        if len(raw) > MAX_CODE_BYTES:
            return jsonify({"error": f"文件超过 {MAX_CODE_BYTES//1024} KB 上限"}), 413
        code = raw.decode("utf-8", errors="replace")
        label = request.form.get("label", "")
    else:
        body = request.get_json(force=True) or {}
        code = body.get("code", "")
        label = body.get("label", "")
        if len(code.encode()) > MAX_CODE_BYTES:
            return jsonify({"error": f"代码超过 {MAX_CODE_BYTES//1024} KB 上限"}), 413
        if not code.strip():
            return jsonify({"error": "请上传 .py 文件或在 code 字段提供代码"}), 400
    label = (label or "")[:MAX_LABEL_CHARS]
    # 限流
    conn = get_conn()
    recent = conn.execute("""SELECT COUNT(*) FROM submissions
        WHERE team_id=? AND phase=?
          AND submitted_at > datetime('now','-5 minutes')""",
        (team["id"], phase)).fetchone()[0]
    conn.close()
    if recent >= 1:
        return jsonify({"error": "提交过于频繁，请 5 分钟后再试"}), 429
    fname = f"team{team['id']}_phase{phase}_{int(time.time())}.py"
    path  = UPLOAD_DIR / fname
    path.write_text(code, encoding="utf-8")
    sid = enqueue(team["id"], phase, str(path), label)
    return jsonify({"submission_id": sid,
                    "message": "提交成功，正在排队评测",
                    "poll_url": f"/api/status/{sid}"}), 202
# ── 查询状态 ───────────────────────────────────────────────
@app.route("/api/status/<int:sid>")
@require_auth
def status(sid):
    sub = get_submission(sid)
    if not sub: return jsonify({"error": "not found"}), 404
    if sub["team_id"] != request.team["id"]:
        return jsonify({"error": "无权查看此提交"}), 403
    # 返回 viz 链接（若有）
    if sub.get("viz_path"):
        sub["viz_url"] = f"/viz/{sid}"
    return jsonify(sub)

# ── 本队提交历史 ───────────────────────────────────────────
@app.route("/api/my_submissions")
@require_auth
def my_submissions():
    team = request.team
    phase = request.args.get("phase", CURRENT_PHASE)
    conn = get_conn()
    rows = conn.execute("""SELECT id, submitted_at, status, combined,
        score_F2, score_F3, score_F6, label, error_msg, viz_path
        FROM submissions WHERE team_id=? AND phase=?
        ORDER BY id DESC LIMIT 30""", (team["id"], phase)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["viz_url"] = f"/viz/{d['id']}" if d.get("viz_path") else None
        d.pop("viz_path", None)
        out.append(d)
    return jsonify(out)

# ── 榜单 API ────────────────────────────────────────────────
@app.route("/api/leaderboard")
def leaderboard_api():
    phase = request.args.get("phase", CURRENT_PHASE)
    rows = get_leaderboard(phase)
    for d in rows:
        d["viz_url"] = f"/viz/{d['id']}" if d.pop("viz_path", None) else None
    return jsonify({"phase": phase, "rows": rows})

# ── 3D 可视化（公开：榜单与提交记录都可直接打开）──────────
@app.route("/viz/<int:sid>")
def serve_viz(sid):
    sub = get_submission(sid)
    if not sub or sub.get("status") != "done": abort(404)
    vp = sub.get("viz_path")
    if not vp: abort(404)
    p = pathlib.Path(vp).resolve()
    if p.parent != VIZ_DIR.resolve() or not p.exists(): abort(404)
    return send_file(str(p), mimetype="text/html")

# ── 管理接口：Authorization: Bearer <OJ_ADMIN> ─────────────
def _require_admin():
    hdr = request.headers.get("Authorization", "")
    tok = hdr[7:].strip() if hdr.startswith("Bearer ") else request.headers.get("X-Admin-Secret", "")
    if not _is_admin_token(tok):
        abort(403)

@app.route("/api/admin/teams", methods=["GET"])
def admin_list_teams():
    _require_admin()
    return jsonify(get_all_teams())

@app.route("/api/admin/teams", methods=["POST"])
def admin_add_team():
    _require_admin()
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 40:
        return jsonify({"error": "队伍名称须为 1–40 个字符"}), 400
    return jsonify(create_team(name)), 201

@app.route("/api/admin/teams/<int:tid>", methods=["DELETE"])
def admin_del_team(tid):
    _require_admin()
    delete_team(tid)
    return jsonify({"ok": True})

@app.route("/api/admin/submissions")
def admin_list_submissions():
    _require_admin()
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = get_all_submissions(limit)
    for d in rows:
        d["viz_url"] = f"/viz/{d['id']}" if d.get("viz_path") and d.get("status") == "done" else None
        d.pop("viz_path", None); d.pop("policy_path", None)
    return jsonify(rows)

@app.route("/api/admin/requeue/<int:sid>", methods=["POST"])
def admin_requeue(sid):
    """把 error/running 状态的提交重置为 queued，方便重试。"""
    _require_admin()
    conn = get_conn()
    conn.execute("UPDATE submissions SET status='queued', error_msg=NULL "
                 "WHERE id=? AND status IN ('error','running')", (sid,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

# ── 前端页面（单页应用）────────────────────────────────────
@app.route("/")
@app.route("/submit")
@app.route("/dashboard")
@app.route("/admin")
def index(): return Response(_APP_HTML, mimetype="text/html")

_APP_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>调度黑客松 · OJ</title>
<style>
:root{--bg:#f0f2f5;--card:#fff;--accent:#1a73e8;--text:#1f1f1f;--muted:#888;
--border:#e8eaed;--ok:#34a853;--err:#ea4335;--warn:#f5b942}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Segoe UI",sans-serif;
background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--accent);color:#fff;padding:14px 28px;
display:flex;align-items:center;justify-content:space-between;
box-shadow:0 2px 8px rgba(0,0,0,.18)}
header h1{font-size:18px;font-weight:700}
header .nav{display:flex;gap:18px;align-items:center;font-size:13px}
header .nav a{color:#fff;opacity:.88;cursor:pointer;text-decoration:none}
header .nav a:hover{opacity:1}
header .user{font-size:12px;opacity:.85}
.card{background:var(--card);border-radius:12px;padding:22px 24px;
box-shadow:0 1px 4px rgba(0,0,0,.08);margin:18px 28px}
.muted{color:var(--muted);font-size:13px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
input[type=text],input[type=password],textarea{
width:100%;padding:9px 12px;border:1px solid #ddd;border-radius:8px;
font-size:14px;outline:none;font-family:inherit}
input:focus,textarea:focus{border-color:var(--accent)}
textarea.code{font-family:"JetBrains Mono","Consolas",monospace;font-size:13px;
min-height:280px;line-height:1.5;tab-size:4}
label{display:block;font-size:13px;font-weight:600;margin-bottom:5px;color:#444}
button{padding:9px 18px;background:var(--accent);color:#fff;border:none;
border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#1557b0}
button.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent)}
button.ghost:hover{background:#e8f0fe}
button.small{padding:5px 12px;font-size:12px}
.msg{margin-top:10px;padding:9px 12px;border-radius:8px;font-size:13px;display:none}
.msg.ok{background:#e6f4ea;color:#1e7e34}.msg.err{background:#fce8e6;color:#c62828}
table{width:100%;border-collapse:collapse}
thead tr{background:#f8f9fa;border-bottom:2px solid var(--border)}
th{padding:10px 12px;text-align:left;font-size:11px;font-weight:600;
color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
td{padding:10px 12px;font-size:13px;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8f9fb}
.rank{font-weight:700;width:36px}
.r1{color:#f5b942}.r2{color:#9fa8a3}.r3{color:#cd7f32}
.sc{font-weight:700;color:var(--accent)}
.bar{height:5px;border-radius:3px;background:#e8eaed;margin-top:3px;overflow:hidden}
.bar>div{height:100%;border-radius:3px;background:var(--accent);transition:.4s}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.queued{background:#e8eaed;color:#666}
.badge.running{background:#feefc3;color:#b06000}
.badge.done{background:#e6f4ea;color:#1e7e34}
.badge.error{background:#fce8e6;color:#c62828}
.phase-tabs{display:flex;gap:6px;margin:14px 28px 0}
.tab{padding:6px 16px;border-radius:16px;cursor:pointer;font-size:13px;
font-weight:500;border:2px solid transparent}
.tab.active{background:var(--accent);color:#fff}
.tab:not(.active){background:#fff;color:var(--muted);border-color:var(--border)}
.hidden{display:none}
.hint{font-size:12px;color:var(--muted);margin-top:6px}
a.lk{color:var(--accent);text-decoration:none;font-size:13px}
a.lk:hover{text-decoration:underline}
.empty{text-align:center;padding:40px;color:var(--muted);font-size:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}.card{margin:14px 12px}}
</style></head><body>
<header>
<h1>🏭 单臂柔性机加单元调度黑客松</h1>
<div class="nav">
<a onclick="go('leaderboard')">榜单</a>
<a id="nav-submit" onclick="go('submit')">提交</a>
<a id="nav-dashboard" onclick="go('dashboard')">我的提交</a>
<a id="nav-admin" class="hidden" onclick="go('admin')">管理</a>
<span class="user" id="user-badge"></span>
</div></header>

<div id="view-leaderboard">
<div class="phase-tabs">
<div class="tab active" id="tabA" onclick="setPhase('A')">阶段 A</div>
<div class="tab" id="tabB" onclick="setPhase('B')">阶段 B</div>
</div>
<div class="card">
<div class="row" style="margin-bottom:12px">
<span class="muted" id="lb-count">加载中…</span>
<span class="muted" style="margin-left:auto">每 10s 自动刷新</span>
</div>
<table><thead><tr>
<th>排名</th><th>队伍</th><th style="text-align:right">综合分</th>
<th>F2</th><th>F3</th><th>F6</th><th>提交时间</th><th>备注</th><th>策略动画</th>
</tr></thead><tbody id="lb-body">
<tr><td colspan="9" class="empty">加载中…</td></tr>
</tbody></table>
</div></div>

<div id="view-submit" class="hidden">
<div class="card">
<h3 style="margin-bottom:4px">提交调度策略</h3>
<div class="muted" style="margin-bottom:16px">在编辑器中粘贴代码或上传 .py 文件，提交后自动排队评测。</div>
<div class="grid2">
<div>
<label>代码编辑器（需含 decide(obs) 函数）</label>
<textarea class="code" id="code-input" spellcheck="false"
placeholder="def decide(obs):&#10;    acts = obs['legal_actions']&#10;    ..."></textarea>
<div class="hint">Tab 键插入 4 空格</div>
</div>
<div>
<label>或上传 .py 文件</label>
<input type="file" id="file-input" accept=".py" onchange="onFilePick(event)">
<div style="height:8px"></div>
<label>备注（显示在榜单，可选）</label>
<input type="text" id="label-input" maxlength="128" placeholder="v3 rollout">
<div style="height:12px"></div>
<button onclick="doSubmit()">提交并排队评测</button>
<div class="msg" id="submit-msg"></div>
<div class="hint" id="submit-hint"></div>
</div></div></div></div>

<div id="view-dashboard" class="hidden">
<div class="card">
<div class="row" style="margin-bottom:12px">
<h3>我的提交历史</h3>
<span class="muted" style="margin-left:auto" id="dash-team"></span>
</div>
<table><thead><tr>
<th>#</th><th>状态</th><th style="text-align:right">综合分</th>
<th>F2</th><th>F3</th><th>F6</th><th>提交时间</th><th>备注</th><th>策略动画</th>
</tr></thead><tbody id="dash-body">
<tr><td colspan="9" class="empty">加载中…</td></tr>
</tbody></table></div></div>

<div id="view-admin" class="hidden">
<div class="card">
<h3 style="margin-bottom:12px">队伍管理 <span class="muted" id="team-count"></span></h3>
<div class="row" style="margin-bottom:14px">
<input type="text" id="new-name" maxlength="40" placeholder="新队伍名称" style="width:240px">
<button class="small" onclick="addTeam()">创建队伍</button>
<span class="muted" id="add-msg"></span>
</div>
<table><thead><tr><th>ID</th><th>队伍</th><th>Token</th><th>创建时间</th><th>操作</th></tr></thead>
<tbody id="team-body"></tbody></table>
</div>
<div class="card">
<div class="row" style="margin-bottom:12px">
<h3>全部提交（最近 200 条）</h3>
<button class="small ghost" style="margin-left:auto" onclick="loadAdminSubs()">刷新</button>
</div>
<table><thead><tr><th>#</th><th>队伍</th><th>阶段</th><th>状态</th>
<th style="text-align:right">综合分</th><th>F2</th><th>F3</th><th>F6</th>
<th>提交时间</th><th>错误</th><th>操作</th></tr></thead>
<tbody id="sub-body"></tbody></table>
</div></div>

<div class="card hidden" id="view-login">
<h3 style="margin-bottom:4px">登录 / 注册</h3>
<div class="muted" style="margin-bottom:16px">队伍凭 token 登录；组委会在同一处输入管理员密钥即进入管理界面。</div>
<div class="grid2">
<div>
<label>登录（队伍 token 或管理员密钥）</label>
<input type="password" id="login-token" placeholder="粘贴 token">
<div style="height:8px"></div>
<button onclick="doLogin()">登录</button>
<div class="msg" id="login-msg"></div>
</div>
<div>
<label>新队伍？注册</label>
<input type="text" id="reg-name" maxlength="40" placeholder="队伍名称">
<div style="height:8px"></div>
<button onclick="doRegister()">注册并获取 token</button>
<div class="msg" id="reg-msg"></div>
<div class="hint">注册后 token 只显示一次，请保存。</div>
</div></div></div>

<script>
let phase='A', token=localStorage.getItem('oj_token')||'', role=localStorage.getItem('oj_role')||'';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const authH=()=>({'Authorization':'Bearer '+token});
function go(v){
  if((v==='submit'||v==='dashboard') && role!=='team'){ v = role==='admin' ? 'admin' : 'login'; }
  if(v==='admin' && role!=='admin') v='login';
  ['leaderboard','submit','dashboard','admin','login'].forEach(x=>$('view-'+x).classList.toggle('hidden',x!==v));
  if(v==='leaderboard') loadLB();
  if(v==='dashboard') loadDash();
  if(v==='admin'){ loadTeams(); loadAdminSubs(); }
}
function setPhase(p){phase=p;['A','B'].forEach(x=>$('tab'+x).classList.toggle('active',x===p));loadLB();}
function fmt(v){return(v===null||v===undefined)?'—':(+v).toFixed(1)}
function barW(v){return Math.max(0,Math.min(100,(+v||0)+10))}
function relTime(s){if(!s)return'';const d=new Date(s.replace(' ','T')+'Z'),m=Math.round((new Date()-d)/60000);
  if(m<1)return'刚刚';if(m<60)return m+'分前';const h=Math.floor(m/60);if(h<24)return h+'小时前';return s.slice(5,16);}
function badge(s){const m={queued:['queued','排队中'],running:['running','评测中'],done:['done','完成'],error:['error','错误']};
  const[c,n]=(m[s]||['queued',s]);return `<span class="badge ${c}">${esc(n)}</span>`;}
function vizLink(u){return u?`<a class="lk" href="${esc(u)}" target="_blank">▶ 3D 动画</a>`:'<span class="muted">—</span>';}
function msg(id,ok,text){const m=$(id);m.className='msg '+(ok?'ok':'err');m.style.display='block';m.textContent=text;}
function setSession(t,r,name){token=t;role=r;localStorage.setItem('oj_token',t);localStorage.setItem('oj_role',r);
  localStorage.setItem('oj_name',name||'');refreshUser();}
function refreshUser(){
  const b=$('user-badge'),name=localStorage.getItem('oj_name')||'';
  $('nav-admin').classList.toggle('hidden',role!=='admin');
  $('nav-submit').classList.toggle('hidden',role==='admin');
  $('nav-dashboard').classList.toggle('hidden',role==='admin');
  if(token){b.innerHTML=esc(name||'已登录')+' · <a onclick="doLogout()" style="color:#fff">退出</a>';}
  else{b.innerHTML='<a onclick="go(\'login\')" style="color:#fff">登录/注册</a>';}}
function doLogout(){token='';role='';['oj_token','oj_role','oj_name'].forEach(k=>localStorage.removeItem(k));refreshUser();go('leaderboard');}
async function doLogin(){
  const t=$('login-token').value.trim();if(!t)return;
  try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})});
    const d=await r.json();
    if(!r.ok){msg('login-msg',false,d.error||'登录失败');return;}
    setSession(t,d.role,d.name);$('login-token').value='';
    go(d.role==='admin'?'admin':'dashboard');}
  catch(e){msg('login-msg',false,'网络错误');}}
async function doRegister(){
  const name=$('reg-name').value.trim();if(!name)return;
  try{const r=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    const d=await r.json();
    if(!r.ok){msg('reg-msg',false,d.error||'注册失败');return;}
    setSession(d.token,'team',d.name);
    alert('注册成功！请务必保存你的 token（只显示这一次）：\n\n'+d.token);
    go('dashboard');}
  catch(e){msg('reg-msg',false,'网络错误');}}
function onFilePick(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{$('code-input').value=r.result;};r.readAsText(f);}
async function doSubmit(){
  if(role!=='team'){go('login');return;}
  const code=$('code-input').value.trim(),label=$('label-input').value.trim();
  $('submit-msg').style.display='none';
  if(!code){msg('submit-msg',false,'代码不能为空');return;}
  $('submit-hint').textContent='提交中…';
  try{const r=await fetch('/api/submit',{method:'POST',headers:{...authH(),'Content-Type':'application/json'},body:JSON.stringify({code,label})});
    const d=await r.json();
    if(r.status===401){doLogout();go('login');return;}
    if(r.ok){msg('submit-msg',true,'提交成功 #'+d.submission_id+'，正在排队评测');
      $('submit-hint').textContent='稍后到「我的提交」查看结果。';setTimeout(()=>go('dashboard'),1500);}
    else{msg('submit-msg',false,d.error||'提交失败');$('submit-hint').textContent='';}}
  catch(e){msg('submit-msg',false,'网络错误');}}
async function loadLB(){
  try{const r=await fetch('/api/leaderboard?phase='+phase);const d=await r.json();const rows=d.rows||[];
    $('lb-count').textContent=rows.length+' 支队伍上榜';const b=$('lb-body');
    if(!rows.length){b.innerHTML='<tr><td colspan="9" class="empty">暂无提交</td></tr>';return;}
    b.innerHTML=rows.map((x,i)=>{const rk=i+1,rc=rk<=3?['r1','r2','r3'][rk-1]:'';
      return `<tr><td class="rank ${rc}">${rk<=3?['🥇','🥈','🥉'][rk-1]:rk}</td>
<td style="font-weight:600">${esc(x.name)}</td>
<td style="text-align:right"><div class="sc">${fmt(x.combined)}</div><div class="bar"><div style="width:${barW(x.combined)}%"></div></div></td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td class="muted">${relTime(x.submitted_at)}</td><td class="muted">${esc(x.label)}</td><td>${vizLink(x.viz_url)}</td></tr>`;}).join('');}
  catch(e){console.error(e);}}
async function loadDash(){
  try{const r=await fetch('/api/my_submissions?phase='+phase,{headers:authH()});
    if(!r.ok){doLogout();go('login');return;}
    const rows=await r.json();$('dash-team').textContent='本队最近 30 条';const b=$('dash-body');
    if(!rows.length){b.innerHTML='<tr><td colspan="9" class="empty">暂无提交</td></tr>';return;}
    b.innerHTML=rows.map(x=>`<tr><td>#${x.id}</td><td>${badge(x.status)}</td>
<td style="text-align:right" class="sc">${fmt(x.combined)}</td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td class="muted">${relTime(x.submitted_at)}</td><td class="muted">${esc(x.label)}</td>
<td>${x.status==='error'?`<span class="muted" title="${esc(x.error_msg)}">查看错误</span>`:vizLink(x.viz_url)}</td></tr>`).join('');}
  catch(e){console.error(e);}}
async function adminApi(url,opts={}){
  const r=await fetch(url,{...opts,headers:{...authH(),'Content-Type':'application/json',...(opts.headers||{})}});
  if(r.status===403){doLogout();go('login');throw new Error('forbidden');}
  return r.json();}
async function loadTeams(){
  const rows=await adminApi('/api/admin/teams');$('team-count').textContent=`（${rows.length} 支）`;
  $('team-body').innerHTML=rows.length?rows.map(t=>`<tr><td class="muted">${t.id}</td>
<td style="font-weight:600">${esc(t.name)}</td><td><code style="font-size:12px">${esc(t.token)}</code></td>
<td class="muted">${esc(t.created_at)}</td>
<td><button class="small" style="background:var(--err)" onclick="delTeam(${t.id})">删除</button></td></tr>`).join('')
    :'<tr><td colspan="5" class="empty">暂无队伍</td></tr>';}
async function addTeam(){
  const name=$('new-name').value.trim();if(!name)return;
  const d=await adminApi('/api/admin/teams',{method:'POST',body:JSON.stringify({name})});
  $('add-msg').textContent=d.error?d.error:`已创建「${d.name}」 token: ${d.token}`;
  $('new-name').value='';loadTeams();}
async function delTeam(id){
  if(!confirm('确定删除该队伍及其全部提交？'))return;
  await adminApi('/api/admin/teams/'+id,{method:'DELETE'});loadTeams();loadAdminSubs();}
async function loadAdminSubs(){
  const rows=await adminApi('/api/admin/submissions?limit=200');
  $('sub-body').innerHTML=rows.length?rows.map(x=>`<tr><td>#${x.id}</td>
<td style="font-weight:600">${esc(x.team_name)}</td><td>${esc(x.phase)}</td><td>${badge(x.status)}</td>
<td style="text-align:right" class="sc">${fmt(x.combined)}</td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td class="muted">${relTime(x.submitted_at)}</td>
<td>${x.error_msg?`<span class="muted" title="${esc(x.error_msg)}">${esc(x.error_msg.split('\n').filter(Boolean).pop()).slice(0,60)}</span>`:'—'}</td>
<td>${(x.status==='error'||x.status==='running')?`<button class="small" onclick="requeue(${x.id})">重试</button>`:vizLink(x.viz_url)}</td></tr>`).join('')
    :'<tr><td colspan="11" class="empty">暂无提交</td></tr>';}
async function requeue(sid){await adminApi('/api/admin/requeue/'+sid,{method:'POST'});loadAdminSubs();}
refreshUser();
go(role==='admin'?'admin':(role==='team'?'dashboard':'leaderboard'));
loadLB();setInterval(loadLB,10000);
$('code-input').addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();
  const t=e.target,s=t.selectionStart,en=t.selectionEnd;t.value=t.value.slice(0,s)+'    '+t.value.slice(en);t.selectionStart=t.selectionEnd=s+4;}});
</script></body></html>
"""

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("OJ_PORT", 8080))
    bind = os.environ.get("OJ_BIND", "127.0.0.1")
    print(f"[server] http://{bind}:{port}  phase={CURRENT_PHASE}")
    app.run(host=bind, port=port, debug=False, threaded=True)

