"""OJ HTTP 服务器 v2 — 注册/登录 · 代码编辑器提交 · 实时状态页"""
import sys, os, time, pathlib, collections
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

# ── 登录（凭 token 获取队伍信息）──────────────────────────
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "请提供 token"}), 400
    conn = get_conn()
    row = conn.execute("SELECT id, name, token FROM teams WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "token 无效"}), 401
    return jsonify(dict(row))

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
        sub["viz_url"] = f"/api/viz/{sid}"
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
        if d.get("viz_path"): d["viz_url"] = f"/api/viz/{d['id']}"
        out.append(d)
    return jsonify(out)

# ── 榜单 API ────────────────────────────────────────────────
@app.route("/api/leaderboard")
def leaderboard_api():
    phase = request.args.get("phase", CURRENT_PHASE)
    return jsonify({"phase": phase, "rows": get_leaderboard(phase)})

# ── Gantt 可视化 ───────────────────────────────────────────
@app.route("/api/viz/<int:sid>")
@require_auth
def serve_viz(sid):
    sub = get_submission(sid)
    if not sub: abort(404)
    if sub["team_id"] != request.team["id"]: abort(403)
    vp = sub.get("viz_path")
    if not vp or not os.path.exists(vp): abort(404)
    return send_file(vp, mimetype="text/html")

# ── 管理接口（API）────────────────────────────────────────
def _require_admin():
    secret = (request.headers.get("X-Admin-Secret")
              or request.args.get("secret")
              or (request.get_json(silent=True) or {}).get("secret", ""))
    if secret != ADMIN_SECRET:
        abort(403)

@app.route("/admin/team", methods=["POST"])
def admin_create_team():
    _require_admin()
    data = request.get_json(force=True) or {}
    if "name" not in data: return jsonify({"error": "需要 name 字段"}), 400
    return jsonify(create_team(data["name"])), 201

@app.route("/admin/api/teams", methods=["GET"])
def admin_list_teams():
    _require_admin()
    return jsonify(get_all_teams())

@app.route("/admin/api/teams", methods=["POST"])
def admin_add_team():
    _require_admin()
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name: return jsonify({"error": "需要 name"}), 400
    return jsonify(create_team(name)), 201

@app.route("/admin/api/teams/<int:tid>", methods=["DELETE"])
def admin_del_team(tid):
    _require_admin()
    delete_team(tid)
    return jsonify({"ok": True})

@app.route("/admin/api/submissions")
def admin_list_submissions():
    _require_admin()
    limit = int(request.args.get("limit", 200))
    return jsonify(get_all_submissions(limit))

@app.route("/admin/api/reset_submission/<int:sid>", methods=["POST"])
def admin_reset_submission(sid):
    """把 error/running 状态的提交重置为 queued，方便重试。"""
    _require_admin()
    conn = get_conn()
    conn.execute(
        "UPDATE submissions SET status='queued', error_msg=NULL WHERE id=? AND status IN ('error','running')",
        (sid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ── 管理后台页面 ──────────────────────────────────────────
@app.route("/admin")
@app.route("/admin/")
def admin_page():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return Response(
            '<form method="get"><input name="secret" type="password" placeholder="管理密钥">'
            '<button>进入</button></form>', mimetype="text/html")
    return Response(_ADMIN_HTML.replace("__SECRET__", secret), mimetype="text/html")

# ── Demo 可视化（公开，无需登录）──────────────────────────
DEMO_VIZ_DIR = pathlib.Path(__file__).parent / "demo_viz"
DEMO_VIZ_DIR.mkdir(exist_ok=True)

@app.route("/demo_viz")
def demo_viz_index():
    files = sorted(DEMO_VIZ_DIR.glob("*.html"))
    if not files:
        return Response(
            "<p style='font-family:sans-serif;padding:40px'>暂无演示数据，"
            "请运行 <code>python3 tools/seed_demo.py</code> 生成。</p>",
            mimetype="text/html")
    if len(files) == 1:
        return send_file(str(files[0]), mimetype="text/html")
    links = "".join(
        f'<li><a href="/demo_viz/{f.name}">{f.stem}</a></li>' for f in files)
    return Response(
        f'<ul style="font-family:sans-serif;padding:40px;line-height:2">{links}</ul>',
        mimetype="text/html")

@app.route("/demo_viz/<path:fname>")
def demo_viz_file(fname):
    p = DEMO_VIZ_DIR / pathlib.Path(fname).name
    if not p.exists() or p.suffix != ".html":
        abort(404)
    return send_file(str(p), mimetype="text/html")

# ── 前端页面 ──────────────────────────────────────────────
@app.route("/")
def index(): return Response(_APP_HTML, mimetype="text/html")

@app.route("/submit")
def submit_page(): return Response(_APP_HTML, mimetype="text/html")

@app.route("/dashboard")
def dashboard_page(): return Response(_APP_HTML, mimetype="text/html")

_ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OJ 管理后台</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Segoe UI",sans-serif;background:#f0f2f5;color:#1f1f1f}
header{background:#b71c1c;color:#fff;padding:12px 24px;display:flex;align-items:center;gap:16px}
header h1{font-size:16px;font-weight:700}
.nav{display:flex;gap:16px;font-size:13px}
.nav a{color:#fff;opacity:.85;cursor:pointer}
.nav a:hover{opacity:1}
.card{background:#fff;border-radius:10px;padding:20px 22px;
box-shadow:0 1px 4px rgba(0,0,0,.08);margin:16px 24px}
h2{font-size:15px;font-weight:700;margin-bottom:14px}
input[type=text]{padding:7px 11px;border:1px solid #ddd;border-radius:7px;
font-size:13px;outline:none;width:220px}
input:focus{border-color:#b71c1c}
button{padding:7px 16px;background:#b71c1c;color:#fff;border:none;border-radius:7px;
font-size:13px;font-weight:600;cursor:pointer}
button:hover{background:#8e0000}
button.del{background:#ea4335;padding:4px 10px;font-size:12px}
button.del:hover{background:#c62828}
button.reset{background:#f57c00;padding:4px 10px;font-size:12px}
button.reset:hover{background:#e65100}
table{width:100%;border-collapse:collapse}
thead tr{background:#fafafa;border-bottom:2px solid #e8eaed}
th{padding:9px 10px;text-align:left;font-size:11px;font-weight:600;
color:#888;text-transform:uppercase;letter-spacing:.4px}
td{padding:9px 10px;font-size:13px;border-bottom:1px solid #f0f0f0}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafafa}
.token{font-family:monospace;font-size:12px;color:#555;word-break:break-all}
.sc{font-weight:700;color:#1a73e8}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.queued{background:#e8eaed;color:#666}
.badge.running{background:#feefc3;color:#b06000}
.badge.done{background:#e6f4ea;color:#1e7e34}
.badge.error{background:#fce8e6;color:#c62828}
.msg{font-size:12px;color:#1e7e34;margin-left:10px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.err-txt{font-size:11px;color:#c62828;max-width:300px;overflow:hidden;
white-space:nowrap;text-overflow:ellipsis}
</style></head><body>
<header>
<h1>🔧 OJ 管理后台</h1>
<div class="nav">
<a onclick="showTab('teams')">队伍管理</a>
<a onclick="showTab('subs')">提交记录</a>
<a href="/demo_viz" target="_blank">Demo 可视化</a>
<a href="/" target="_blank">参赛者页面</a>
</div>
</header>

<div id="tab-teams">
<div class="card">
<h2>添加队伍</h2>
<div class="row">
<input type="text" id="new-name" placeholder="队伍名称（支持中文）">
<button onclick="addTeam()">创建并获取 token</button>
<span class="msg" id="add-msg"></span>
</div>
</div>
<div class="card">
<h2>所有队伍 <span id="team-count" style="font-weight:400;color:#888;font-size:13px"></span></h2>
<table><thead><tr>
<th>ID</th><th>队伍名称</th><th>Token</th><th>注册时间</th><th>操作</th>
</tr></thead><tbody id="team-body">
<tr><td colspan="5" style="text-align:center;padding:30px;color:#888">加载中…</td></tr>
</tbody></table>
</div>
</div>

<div id="tab-subs" style="display:none">
<div class="card">
<div class="row" style="margin-bottom:0">
<h2 style="margin-bottom:0">最近 200 条提交</h2>
<button class="reset" style="margin-left:auto" onclick="loadSubs()">刷新</button>
</div>
</div>
<div class="card">
<table><thead><tr>
<th>#</th><th>队伍</th><th>阶段</th><th>状态</th>
<th style="text-align:right">综合分</th><th>F2</th><th>F3</th><th>F6</th>
<th>提交时间</th><th>错误</th><th>操作</th>
</tr></thead><tbody id="sub-body">
<tr><td colspan="11" style="text-align:center;padding:30px;color:#888">加载中…</td></tr>
</tbody></table>
</div>
</div>

<script>
const SECRET = '__SECRET__';
const qs = k => document.getElementById(k);
function fmt(v){return(v===null||v===undefined)?'—':(+v).toFixed(1);}
function badge(s){const m={queued:'排队',running:'评测中',done:'完成',error:'错误'};
return `<span class="badge ${s}">${m[s]||s}</span>`;}
function relTime(s){if(!s)return'';
const d=new Date(s.replace(' ','T')+'Z'),now=new Date();
const m=Math.round((now-d)/60000);
if(m<1)return'刚刚';if(m<60)return m+'分前';
const h=Math.floor(m/60);if(h<24)return h+'小时前';return s.slice(5,16);}
function showTab(t){
qs('tab-teams').style.display=t==='teams'?'':'none';
qs('tab-subs').style.display=t==='subs'?'':'none';
if(t==='teams')loadTeams();else loadSubs();}
async function api(url,opts={}){
const sep=url.includes('?')?'&':'?';
return fetch(url+sep+'secret='+SECRET,opts).then(r=>r.json());}
async function loadTeams(){
const rows=await api('/admin/api/teams');
qs('team-count').textContent=`(${rows.length} 支)`;
const b=qs('team-body');
if(!rows.length){b.innerHTML='<tr><td colspan="5" style="text-align:center;padding:30px;color:#888">暂无队伍</td></tr>';return;}
b.innerHTML=rows.map(t=>`<tr>
<td style="color:#888">${t.id}</td>
<td style="font-weight:600">${t.name}</td>
<td class="token">${t.token}</td>
<td style="color:#888">${t.created_at||''}</td>
<td><button class="del" onclick="delTeam(${t.id},'${t.name.replace(/'/g,'\\'')}')">删除</button></td>
</tr>`).join('');}
async function addTeam(){
const name=qs('new-name').value.trim();if(!name)return;
const r=await api('/admin/api/teams',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({name})});
qs('add-msg').textContent=r.token?`token: ${r.token}`:(r.error||'已存在');
qs('new-name').value='';loadTeams();}
async function delTeam(id,name){
if(!confirm(`确定删除队伍「${name}」及其所有提交？`))return;
await api('/admin/api/teams/'+id,{method:'DELETE'});loadTeams();}
async function loadSubs(){
const rows=await api('/admin/api/submissions?limit=200');
const b=qs('sub-body');
if(!rows.length){b.innerHTML='<tr><td colspan="11" style="text-align:center;padding:30px;color:#888">暂无提交</td></tr>';return;}
b.innerHTML=rows.map(x=>`<tr>
<td>#${x.id}</td>
<td style="font-weight:600">${x.team_name}</td>
<td>${x.phase}</td>
<td>${badge(x.status)}</td>
<td style="text-align:right" class="sc">${fmt(x.combined)}</td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td style="color:#888">${relTime(x.submitted_at)}</td>
<td>${x.error_msg?`<span class="err-txt" title="${x.error_msg}">${x.error_msg}</span>`:'—'}</td>
<td>${(x.status==='error'||x.status==='running')?
`<button class="reset" onclick="resetSub(${x.id})">重试</button>`:'—'}
${x.viz_path?`<a href="/demo_viz" target="_blank" style="font-size:12px;margin-left:6px">viz</a>`:''}
</td>
</tr>`).join('');}
async function resetSub(sid){
await api('/admin/api/reset_submission/'+sid,{method:'POST'});loadSubs();}
loadTeams();
</script></body></html>
"""

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
<a onclick="go('submit')">提交</a>
<a onclick="go('dashboard')">我的提交</a>
<a href="/demo_viz" target="_blank">演示</a>
<a href="/admin" target="_blank">管理</a>
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
<th>F2</th><th>F3</th><th>F6</th><th>提交时间</th><th>备注</th>
</tr></thead><tbody id="lb-body">
<tr><td colspan="8" class="empty">加载中…</td></tr>
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
<input type="file" id="file-input" accept=".py"
 onchange="onFilePick(event)">
<div style="height:8px"></div>
<label>备注（显示在榜单，可选）</label>
<input type="text" id="label-input" placeholder="v3 rollout">
<div style="height:12px"></div>
<button onclick="doSubmit()">提交并排队评测</button>
<button class="ghost" onclick="loadTemplate()">载入示例</button>
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
<th>F2</th><th>F3</th><th>F6</th><th>提交时间</th><th>备注</th><th>可视化</th>
</tr></thead><tbody id="dash-body">
<tr><td colspan="9" class="empty">请先登录</td></tr>
</tbody></table></div></div>

<div class="card hidden" id="view-login">
<h3 style="margin-bottom:4px">登录 / 注册</h3>
<div class="muted" style="margin-bottom:16px">凭 token 登录，或注册新队伍。</div>
<div class="grid2">
<div>
<label>已有 token？登录</label>
<input type="password" id="login-token" placeholder="粘贴 token">
<button onclick="doLogin()">登录</button>
<div class="msg" id="login-msg"></div>
</div>
<div>
<label>新队伍？注册</label>
<input type="text" id="reg-name" placeholder="队伍名称">
<button onclick="doRegister()">注册并获取 token</button>
<div class="msg" id="reg-msg"></div>
<div class="hint">注册后 token 只显示一次，请保存。</div>
</div></div></div></div>

<script>
let phase='A', token=localStorage.getItem('oj_token')||'';
const $=id=>document.getElementById(id);
function go(v){
['leaderboard','submit','dashboard','login'].forEach(x=>{
$('view-'+x).classList.toggle('hidden', x!==v);});
if(v==='leaderboard') loadLB();
if(v==='submit'){}
if(v==='dashboard') loadDash();
if(v==='login'){}
}
function setPhase(p){phase=p;
['A','B'].forEach(x=>$('tab'+x).classList.toggle('active',x===p));
loadLB();}
function fmt(v){if(v===null||v===undefined)return'—';return(+v).toFixed(1)}
function barW(v){return Math.max(0,Math.min(100,(+v||0)+10))}
function relTime(s){if(!s)return'';
const d=new Date(s.replace(' ','T')+'Z'),now=new Date();
const m=Math.round((now-d)/60000);
if(m<1)return'刚刚';if(m<60)return m+'分前';
const h=Math.floor(m/60);if(h<24)return h+'小时前';
return s.slice(5,16);}
function badge(s){const m={queued:['queued','排队中'],running:['running','评测中'],
done:['done','完成'],error:['error','错误']};
const[c,n]=(m[s]||['queued',s]);return `<span class="badge ${c}">${n}</span>`;}
function refreshUser(){
const b=$('user-badge');
if(token){b.innerHTML='已登录 · <a onclick="doLogout()" style="color:#fff">退出</a>';}
else{b.innerHTML='<a onclick="go(\'login\')" style="color:#fff">登录/注册</a>';}}
function doLogout(){token='';localStorage.removeItem('oj_token');refreshUser();go('login');}
async function doLogin(){
const t=$('login-token').value.trim(); if(!t)return;
try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})});
const d=await r.json();
if(r.ok){token=t;localStorage.setItem('oj_token',t);
$('login-msg').className='msg ok';$('login-msg').style.display='block';
$('login-msg').textContent='欢迎 '+d.name;refreshUser();go('dashboard');}
else{$('login-msg').className='msg err';$('login-msg').style.display='block';$('login-msg').textContent=d.error;}}
catch(e){console.error(e);}}
async function doRegister(){
const name=$('reg-name').value.trim();if(!name)return;
try{const r=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
const d=await r.json();
if(r.ok){token=d.token;localStorage.setItem('oj_token',token);
$('reg-msg').className='msg ok';$('reg-msg').style.display='block';
$('reg-msg').innerHTML='注册成功！token: <code>'+d.token+'</code>（已自动登录）';refreshUser();go('dashboard');}
else{$('reg-msg').className='msg err';$('reg-msg').style.display='block';$('reg-msg').textContent=d.error;}}
catch(e){console.error(e);}}
function onFilePick(e){const f=e.target.files[0];if(!f)return;
const r=new FileReader();r.onload=()=>{$('code-input').value=r.result;};
r.readAsText(f);}
async function loadTemplate(){
const r=await fetch('/example_policy.py');if(!r.ok)return;
$('code-input').value=await r.text();}
async function doSubmit(){
if(!token){go('login');return;}
const code=$('code-input').value.trim();
const label=$('label-input').value.trim();
const m=$('submit-msg');m.style.display='none';
if(!code){m.className='msg err';m.style.display='block';m.textContent='代码不能为空';return;}
$('submit-hint').textContent='提交中…';
try{const r=await fetch('/api/submit',{method:'POST',
headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
body:JSON.stringify({code,label})});
const d=await r.json();
if(r.ok){m.className='msg ok';m.style.display='block';
m.textContent='提交成功 #'+d.submission_id+'，正在排队评测';
$('submit-hint').textContent='评测中，请稍后到「我的提交」查看。';
setTimeout(()=>{go('dashboard');loadDash();},1500);}
else{m.className='msg err';m.style.display='block';m.textContent=d.error;
$('submit-hint').textContent='';}}
catch(e){m.className='msg err';m.style.display='block';m.textContent='网络错误';}}
async function loadLB(){
try{const r=await fetch('/api/leaderboard?phase='+phase);
const d=await r.json();const rows=d.rows||[];
$('lb-count').textContent=rows.length+' 支队伍上榜';
const b=$('lb-body');
if(!rows.length){b.innerHTML='<tr><td colspan="8" class="empty">暂无提交</td></tr>';return;}
b.innerHTML=rows.map((x,i)=>{const rk=i+1;
const rc=rk<=3?['r1','r2','r3'][rk-1]:'';
const bar=`<div class="bar"><div style="width:${barW(x.combined)}%"></div></div>`;
return `<tr><td class="rank ${rc}">${rk<=3?['🥇','🥈','🥉'][rk-1]:rk}</td>
<td style="font-weight:600">${x.name}</td>
<td style="text-align:right"><div class="sc">${fmt(x.combined)}</div>${bar}</td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td class="muted">${relTime(x.submitted_at)}</td><td class="muted">${x.label||''}</td></tr>`;}).join('');}
catch(e){console.error(e);}}
async function loadDash(){
if(!token){go('login');return;}
$('dash-team').textContent='';
try{const r=await fetch('/api/my_submissions?phase='+phase,
{headers:{'Authorization':'Bearer '+token}});
if(!r.ok){token='';localStorage.removeItem('oj_token');refreshUser();go('login');return;}
const rows=await r.json();
$('dash-team').textContent='本队最近 30 条';
const b=$('dash-body');
if(!rows.length){b.innerHTML='<tr><td colspan="9" class="empty">暂无提交</td></tr>';return;}
b.innerHTML=rows.map(x=>`<tr>
<td>#${x.id}</td><td>${badge(x.status)}</td>
<td style="text-align:right" class="sc">${fmt(x.combined)}</td>
<td>${fmt(x.score_F2)}</td><td>${fmt(x.score_F3)}</td><td>${fmt(x.score_F6)}</td>
<td class="muted">${relTime(x.submitted_at)}</td><td class="muted">${x.label||''}</td>
<td>${x.viz_url?`<a class="lk" href="${x.viz_url}" target="_blank">Gantt</a>`:'—'}</td>
</tr>`).join('');}
catch(e){console.error(e);}}
refreshUser();
if(token) go('dashboard'); else go('leaderboard');
loadLB(); setInterval(loadLB,10000);
$('code-input').addEventListener('keydown',e=>{
if(e.key==='Tab'){e.preventDefault();
const t=e.target,s=t.selectionStart,en=t.selectionEnd;
t.value=t.value.slice(0,s)+'    '+t.value.slice(en);
t.selectionStart=t.selectionEnd=s+4;}});
</script></body></html>
"""

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("OJ_PORT", 8080))
    print(f"[server] http://0.0.0.0:{port}  phase={CURRENT_PHASE}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

