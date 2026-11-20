"""
离散事件仿真引擎（整数毫秒）

机制：M1 柔性路线 | M2 换型 | M3 换刀 | M6 外生扰动
事件阶段顺序（同 t_ms）：1=完成  2=扰动  3=到达  4=决策
"""
from __future__ import annotations
import heapq, itertools, copy
from dataclasses import dataclass, field
from typing import Any

# ── 枚举常量 ──────────────────────────────────────────────
IDLE      = "IDLE"
PROC      = "PROCESSING"
SETUP     = "SETUP"
DOWN      = "DOWN"
MOVING    = "MOVING"

PHASE_DONE  = 1   # 完成事件
PHASE_DISRUPT = 2 # 扰动
PHASE_ARRIVE  = 3 # 订单到达
PHASE_DECIDE  = 4 # 策略决策

# ── 数据类 ────────────────────────────────────────────────
@dataclass
class PartState:
    id: str
    type: str
    next_op: int        # 下一道工序索引；== len(ops) 表示全部完成
    location: str       # "IN" | "OUT" | "BUF" | machine_id
    in_transit: bool = False
    on_machine_done: bool = False  # 加工完成但还没被取走

@dataclass
class MachineState:
    id: str
    state: str = IDLE       # IDLE/PROC/SETUP/DOWN
    part_id: str | None = None
    setup_from: str | None = None
    parts_since_tc: int = 0
    down_until_ms: int | None = None
    pending_done_seq: int | None = None   # machine_done 事件序号，用于作废
    pending_setup_seq: int | None = None  # setup_done  事件序号，用于作废
    interrupted_proc: bool = False        # 停机时正在加工（恢复后需作废本次工序）

@dataclass
class RobotState:
    pos: str = "IN"
    state: str = IDLE
    holding: str | None = None   # part_id
    move_end_ms: int = 0

@dataclass
class SimState:
    t_ms: int = 0
    robot: RobotState = field(default_factory=RobotState)
    machines: dict[str, MachineState] = field(default_factory=dict)
    buffer: list[str] = field(default_factory=list)   # part_ids
    buffer_cap: int = 2
    parts: dict[str, PartState] = field(default_factory=dict)
    orders: dict[str, Any] = field(default_factory=dict)
    orders_done: set[str] = field(default_factory=set)
    part_to_order: dict[str, str] = field(default_factory=dict)
    orders_released: set[str] = field(default_factory=set)
    # 结果追踪
    order_flow: dict[str, int] = field(default_factory=dict)  # order_id -> flow_ms
    # 作废的完成事件（用于加工中故障）
    cancelled_completions: set[int] = field(default_factory=set)  # event seq ids
    # 可视化用：资源占用区间与点事件
    intervals: list = field(default_factory=list)
    events: list = field(default_factory=list)


# ── 引擎 ─────────────────────────────────────────────────
class Engine:
    def __init__(self, scenario: dict):
        self.sc = scenario
        self._counter = itertools.count()
        self._eq: list = []        # heapq: (t, phase, seq, evt)
        self.state = SimState()
        self.horizon_ms: int = scenario["horizon_ms"]
        self._build_state()

    # ── 初始化 ──────────────────────────────────────────
    def _build_state(self):
        sc = self.sc
        s = self.state
        s.robot = RobotState(pos=sc["robot"].get("start_pos", "IN"))
        s.buffer_cap = sc.get("buffer_cap", 2)

        for m in sc["machines"]:
            s.machines[m["id"]] = MachineState(id=m["id"])

        # 安排订单到达
        for o in sc["orders"]:
            self._push(o["release_ms"], PHASE_ARRIVE, {"kind": "order_arrive", "order": o})

        # 安排扰动
        for d in sc.get("disruptions", []):
            self._push(d["start_ms"], PHASE_DISRUPT,
                       {"kind": "machine_down", "machine": d["machine"],
                        "duration_ms": d["duration_ms"]})

    # ── 事件队列 ────────────────────────────────────────
    def _push(self, t: int, phase: int, evt: dict) -> int:
        seq = next(self._counter)
        heapq.heappush(self._eq, (t, phase, seq, evt))
        return seq

    def _pop(self) -> tuple[int, dict] | None:
        if not self._eq:
            return None
        t, phase, _, evt = heapq.heappop(self._eq)
        return t, evt

    # ── 决策点 ──────────────────────────────────────────
    def _schedule_decide(self, t: int):
        self._push(t, PHASE_DECIDE, {"kind": "decide"})

    # ── 合法动作 ────────────────────────────────────────
    def legal_actions(self) -> list[dict]:
        s = self.state
        sc = self.sc
        acts: list[dict] = []
        aid = 0
        pt_types = sc["part_types"]
        tc_cfg = {m["id"]: m for m in sc["machines"]}

        if s.robot.state != IDLE:
            acts.append({"action_id": aid, "kind": "wait"})
            return acts

        # 可以被取走的 part
        movable: list[PartState] = []
        for p in s.parts.values():
            if p.in_transit:
                continue
            if p.location == "IN" or p.location == "BUF":
                movable.append(p)
            elif p.on_machine_done:
                movable.append(p)

        for p in movable:
            ops = pt_types[p.type]["ops"]
            # 送往机床加工
            if p.next_op < len(ops):
                compat = ops[p.next_op]["compat"]
                for mid, m in s.machines.items():
                    if mid not in compat:
                        continue
                    if m.state != IDLE:
                        continue
                    if m.part_id is not None:
                        continue
                    # 计算换型时间
                    setup_ms = 0
                    if m.setup_from is not None and m.setup_from != p.type:
                        setup_ms = (sc.get("setup_ms", {})
                                      .get(mid, {})
                                      .get(m.setup_from, {})
                                      .get(p.type, 0))
                    acts.append({"action_id": aid, "kind": "move",
                                 "part": p.id, "from": p.location, "to": mid,
                                 "nominal_ms": compat[mid],
                                 "setup_ms": setup_ms,
                                 "machine_setup_from": m.setup_from})
                    aid += 1
            # 送往 OUT（全部完成）
            if p.next_op == len(ops):
                acts.append({"action_id": aid, "kind": "move",
                             "part": p.id, "from": p.location, "to": "OUT",
                             "nominal_ms": 0, "setup_ms": 0,
                             "machine_setup_from": None})
                aid += 1
            # 送往缓冲区
            if p.on_machine_done and len(s.buffer) < s.buffer_cap:
                acts.append({"action_id": aid, "kind": "move",
                             "part": p.id, "from": p.location, "to": "BUF",
                             "nominal_ms": 0, "setup_ms": 0,
                             "machine_setup_from": None})
                aid += 1

        # 换刀
        for mid, m in s.machines.items():
            cfg = tc_cfg.get(mid, {})
            tc_every = cfg.get("tool_change_every", 9999)
            if m.state == IDLE and m.parts_since_tc >= tc_every:
                acts.append({"action_id": aid, "kind": "tool_change",
                             "machine": mid})
                aid += 1

        acts.append({"action_id": aid, "kind": "wait"})
        return acts

    # ── 执行动作 ────────────────────────────────────────
    def apply_action(self, act: dict) -> str | None:
        """执行动作；返回错误字符串（None 表示成功）。"""
        s = self.state
        sc = self.sc
        kind = act["kind"]

        if kind == "wait":
            return None   # 推进由主循环处理

        if s.robot.state != IDLE:
            return "robot_busy"

        if kind == "move":
            return self._do_move(act)
        if kind == "tool_change":
            return self._do_tool_change(act)

        return f"unknown_action:{kind}"

    def _travel(self, frm: str, to: str) -> int:
        t = self.sc["travel_ms"]
        if frm == to:
            return 0
        try:
            return t[frm][to]
        except KeyError:
            # 对称回退
            try:
                return t[to][frm]
            except KeyError:
                return 5000   # 默认 5s（不应发生）

    def _do_move(self, act: dict) -> str | None:
        s = self.state
        sc = self.sc
        p_id, frm, to = act["part"], act["from"], act["to"]

        p = s.parts.get(p_id)
        if p is None:
            return "unknown_part"
        if p.in_transit:
            return "part_in_transit"
        if p.location != frm:
            return f"part_not_at:{frm}(actual:{p.location})"

        # 验证目标
        if to not in ("OUT", "BUF") and to not in s.machines:
            return f"invalid_dest:{to}"
        if to in s.machines:
            m = s.machines[to]
            if m.state != IDLE:
                return f"machine_not_idle:{to}"
            ops = sc["part_types"][p.type]["ops"]
            if p.next_op >= len(ops):
                return "part_fully_done"
            if to not in ops[p.next_op]["compat"]:
                return f"incompatible_machine:{to}"
        if to == "BUF":
            if len(s.buffer) >= s.buffer_cap:
                return "buffer_full"
            if not p.on_machine_done:
                return "part_not_done_on_machine"
        if to == "OUT":
            ops = sc["part_types"][p.type]["ops"]
            if p.next_op < len(ops):
                return "part_not_fully_done"

        # 计算时长
        pick_ms   = sc["robot"]["pick_ms"]
        place_ms  = sc["robot"]["place_ms"]
        travel1   = self._travel(s.robot.pos, frm)
        travel2   = self._travel(frm, to)
        dur = travel1 + pick_ms + travel2 + place_ms

        end_ms = s.t_ms + dur
        s.robot.state   = MOVING
        s.robot.holding = p_id
        s.robot.move_end_ms = end_ms
        p.in_transit    = True
        if p.on_machine_done:
            # 释放机床（工件离开机床位）
            mid = p.location
            s.machines[mid].part_id = None
            s.machines[mid].state   = IDLE

        s.intervals.append({"resource": "ROBOT", "kind": "move",
                             "part": p_id, "from": frm, "to": to,
                             "t_start": s.t_ms, "t_end": end_ms})
        self._push(end_ms, PHASE_DONE,
                   {"kind": "robot_done", "part": p_id, "dest": to})
        return None

    def _do_tool_change(self, act: dict) -> str | None:
        s = self.state
        sc = self.sc
        mid = act["machine"]
        m = s.machines.get(mid)
        if m is None:
            return "unknown_machine"
        if m.state != IDLE:
            return "machine_not_idle"
        cfg = {mc["id"]: mc for mc in sc["machines"]}.get(mid, {})
        tc_ms = cfg.get("tool_change_ms", 30000)
        travel = self._travel(s.robot.pos, mid)
        end_ms = s.t_ms + travel + tc_ms
        s.robot.state = MOVING
        s.robot.move_end_ms = end_ms
        self._push(end_ms, PHASE_DONE,
                   {"kind": "tool_done", "machine": mid, "robot_dest": mid})
        return None

    # ── 事件处理 ────────────────────────────────────────
    def _handle_event(self, t: int, evt: dict) -> bool:
        """处理一个事件。返回 True 表示需要调用决策。"""
        s = self.state
        sc = self.sc
        kind = evt["kind"]

        if kind == "robot_done":
            return self._on_robot_done(t, evt)
        if kind == "tool_done":
            return self._on_tool_done(t, evt)
        if kind == "machine_done":
            return self._on_machine_done(t, evt)
        if kind == "setup_done":
            return self._on_setup_done(t, evt)
        if kind == "machine_down":
            return self._on_machine_down(t, evt)
        if kind == "machine_recover":
            return self._on_machine_recover(t, evt)
        if kind == "order_arrive":
            return self._on_order_arrive(t, evt)
        return False

    def _on_robot_done(self, t: int, evt: dict) -> bool:
        s = self.state
        sc = self.sc
        p_id = evt["part"]
        dest = evt["dest"]
        p = s.parts[p_id]
        p.in_transit = False
        s.robot.state = IDLE
        s.robot.pos   = dest
        s.robot.holding = None

        if dest == "OUT":
            p.location = "OUT"
            p.on_machine_done = False
            self._check_order_done(p)
            return True

        if dest == "BUF":
            p.location = "BUF"
            p.on_machine_done = False
            s.buffer.append(p_id)
            return True

        # 目标是机床
        mid = dest
        m   = s.machines[mid]
        p.location   = mid
        p.on_machine_done = False
        m.part_id = p_id

        ops = sc["part_types"][p.type]["ops"]
        nom_ms = ops[p.next_op]["compat"][mid]
        setup_ms = 0

        # M2 换型
        if m.setup_from is not None and m.setup_from != p.type:
            setup_ms = (sc.get("setup_ms", {})
                          .get(mid, {})
                          .get(m.setup_from, {})
                          .get(p.type, 0))

        if setup_ms > 0:
            m.state = SETUP
            s.intervals.append({"resource": mid, "kind": "setup",
                                 "part": p_id, "type": p.type,
                                 "t_start": t, "t_end": t + setup_ms})
            seq = self._push(t + setup_ms, PHASE_DONE,
                             {"kind": "setup_done", "machine": mid,
                              "nom_ms": nom_ms})
            m.pending_setup_seq = seq
        else:
            m.state = PROC
            m.setup_from = p.type
            m.parts_since_tc += 1
            s.intervals.append({"resource": mid, "kind": "proc",
                                 "part": p_id, "type": p.type,
                                 "op": p.next_op,
                                 "t_start": t, "t_end": t + nom_ms})
            seq = self._push(t + nom_ms, PHASE_DONE,
                             {"kind": "machine_done", "machine": mid})
            m.pending_done_seq = seq
        return True

    def _on_tool_done(self, t: int, evt: dict) -> bool:
        s = self.state
        mid = evt["machine"]
        s.machines[mid].parts_since_tc = 0
        s.robot.state = IDLE
        s.robot.pos   = evt["robot_dest"]
        s.robot.holding = None
        return True

    def _on_setup_done(self, t: int, evt: dict) -> bool:
        s = self.state
        mid  = evt["machine"]
        m    = s.machines[mid]
        if m.part_id is None or m.state == DOWN:
            return False
        p    = s.parts[m.part_id]
        m.state      = PROC
        m.setup_from = p.type
        m.parts_since_tc += 1
        m.pending_setup_seq = None
        seq = self._push(t + evt["nom_ms"], PHASE_DONE,
                         {"kind": "machine_done", "machine": mid})
        m.pending_done_seq = seq
        return False

    def _on_machine_done(self, t: int, evt: dict) -> bool:
        s = self.state
        mid = evt["machine"]
        m   = s.machines[mid]
        # 防守：停机时漏掉取消的 stale 事件，直接丢弃
        if m.part_id is None or m.state == DOWN:
            return False
        p   = s.parts[m.part_id]
        p.next_op += 1
        m.state = IDLE
        m.pending_done_seq = None
        p.on_machine_done = True
        return True

    def _on_machine_down(self, t: int, evt: dict) -> bool:
        s   = self.state
        mid = evt["machine"]
        m   = s.machines[mid]
        dur = evt["duration_ms"]
        m.down_until_ms = t + dur
        # 作废未完成的加工完成事件
        if m.pending_done_seq is not None:
            s.cancelled_completions.add(m.pending_done_seq)
            m.pending_done_seq = None
        # 作废未完成的换型事件
        if m.pending_setup_seq is not None:
            s.cancelled_completions.add(m.pending_setup_seq)
            m.pending_setup_seq = None
        # 记录停机时是否正在加工或换型（用于恢复时回退工序）
        m.interrupted_proc = m.state in (PROC, SETUP)
        m.state = DOWN
        s.intervals.append({"resource": mid, "kind": "down",
                             "t_start": t, "t_end": t + dur})
        s.events.append({"t": t, "kind": "machine_down",
                         "machine": mid, "duration_ms": dur})
        self._push(t + dur, PHASE_DISRUPT,
                   {"kind": "machine_recover", "machine": mid})
        return False

    def _on_machine_recover(self, t: int, evt: dict) -> bool:
        s   = self.state
        mid = evt["machine"]
        m   = s.machines[mid]
        # 若停机时正在加工/换型：工序作废，工件留在机床上等取走
        if m.interrupted_proc and m.part_id is not None:
            p = s.parts[m.part_id]
            p.on_machine_done = True  # 可以被取走
            # next_op 不递增（工序作废，需重做）
        m.state = IDLE
        m.down_until_ms = None
        m.interrupted_proc = False
        s.events.append({"t": t, "kind": "machine_recover", "machine": mid})
        return True

    def _on_order_arrive(self, t: int, evt: dict) -> bool:
        s = self.state
        o = evt["order"]
        s.orders[o["id"]] = {**o, "release_ms": t,
                             "done_parts": 0, "total_parts": len(o["parts"])}
        s.orders_released.add(o["id"])
        for part in o["parts"]:
            p = PartState(id=part["id"], type=part["type"],
                          next_op=0, location="IN")
            s.parts[part["id"]] = p
            s.part_to_order[part["id"]] = o["id"]
        s.events.append({"t": t, "kind": "order_arrive",
                         "order": o["id"], "n_parts": len(o["parts"])})
        return True

    def _check_order_done(self, p: PartState):
        s = self.state
        sc = self.sc
        ops = sc["part_types"][p.type]["ops"]
        if p.next_op < len(ops):
            return
        oid = s.part_to_order.get(p.id)
        if oid is None:
            return
        o = s.orders[oid]
        o["done_parts"] += 1
        if o["done_parts"] >= o["total_parts"]:
            s.orders_done.add(oid)
            flow = s.t_ms - o["release_ms"]
            s.order_flow[oid] = flow

    # ── 主循环（生成器，逐决策点 yield）────────────────
    def run(self):
        """
        yield observation_dict；调用方 send(action_dict) 执行动作。
        最终 return result_dict。
        """
        s = self.state

        while True:
            if not self._eq:
                break

            t, phase, seq, evt = self._eq[0]

            if t > self.horizon_ms:
                break

            heapq.heappop(self._eq)

            # 跳过已作废的事件（加工中故障）
            if seq in s.cancelled_completions:
                s.cancelled_completions.discard(seq)
                continue

            s.t_ms = t

            if evt["kind"] == "decide":
                need_decide = True
            else:
                need_decide = self._handle_event(t, evt)

            # 检查所有订单是否完成
            if len(s.orders_done) >= len(self.sc["orders"]):
                break

            if need_decide and s.robot.state == IDLE:
                acts = self.legal_actions()
                non_wait = [a for a in acts if a["kind"] != "wait"]
                if non_wait:
                    obs = self._build_obs(acts)
                    action = yield obs
                    if action is None:
                        action = {"action_id": len(acts) - 1}  # wait
                    err = self.apply_action(acts[action["action_id"]])
                    if err:
                        return {"valid": False, "error": err, "t_ms": t}

        return self._final_result()

    def _build_obs(self, acts: list[dict]) -> dict:
        s = self.state
        return {
            "t_ms": s.t_ms,
            "robot": {
                "pos": s.robot.pos,
                "holding": s.robot.holding,
                "state": s.robot.state,
            },
            "machines": [
                {"id": m.id, "state": m.state,
                 "part": m.part_id,
                 "setup_from": m.setup_from,
                 "parts_since_tc": m.parts_since_tc,
                 "down_until": m.down_until_ms}
                for m in s.machines.values()
            ],
            "buffer": list(s.buffer),
            "parts": {
                pid: {"type": p.type, "next_op": p.next_op,
                      "location": p.location, "on_machine_done": p.on_machine_done}
                for pid, p in s.parts.items()
            },
            "orders": {
                oid: {"release_ms": o["release_ms"],
                      "done_parts": o["done_parts"],
                      "total_parts": o["total_parts"]}
                for oid, o in s.orders.items()
                if oid in s.orders_released
            },
            "legal_actions": acts,
        }

    def _final_result(self) -> dict:
        s = self.state
        all_released = s.orders_released
        if not all_released:
            return {"valid": True, "mean_flow_ms": 0, "orders_done": 0}
        done_flows = [s.order_flow[oid] for oid in all_released
                      if oid in s.orders_done]
        if len(done_flows) < len(all_released):
            # 未完成的订单 flow = horizon
            undone = len(all_released) - len(done_flows)
            done_flows += [self.horizon_ms] * undone
        mean_flow = int(sum(done_flows) / len(done_flows))
        return {
            "valid": True,
            "mean_flow_ms": mean_flow,
            "orders_done": len(s.orders_done),
            "orders_total": len(all_released),
            "t_end_ms": s.t_ms,
            "intervals": s.intervals,
            "events": s.events,
            "order_flow": dict(s.order_flow),
        }
