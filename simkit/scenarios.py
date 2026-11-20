"""
세 가지 시나리오 패밀리 정의: F2 단일 병목 | F3 고 셋업 | F6 외란
패밀리당 개발 인스턴스 3개, 고정 시드.
"""
import random

# ── 공통 공정 경로 ──────────────────────────────────────
# 부품 A: op0 → M1/M2(유연), op1 → M3
# 부품 B: op0 → M1(빠름)/M3(느림, 비상용), op1 → M2/M3
# 부품 C: op0 → M2/M3, op1 → M1/M2
# 모든 공정에 대안 머신이 있어서 정지 시 경로 전환 가능
BASE_PART_TYPES = {
    "A": {"ops": [{"compat": {"M1": 30000, "M2": 36000}},
                  {"compat": {"M3": 24000, "M2": 28000}}]},
    "B": {"ops": [{"compat": {"M1": 28000, "M3": 44000}},   # M3는 비상 대안 (느림)
                  {"compat": {"M2": 22000, "M3": 26000}}]},
    "C": {"ops": [{"compat": {"M2": 32000, "M3": 32000}},
                  {"compat": {"M1": 20000, "M2": 25000}}]},
}

BASE_MACHINES = [
    {"id": "M1", "tool_change_every": 999, "tool_change_ms": 30000},
    {"id": "M2", "tool_change_every": 999, "tool_change_ms": 30000},
    {"id": "M3", "tool_change_every": 999, "tool_change_ms": 30000},
]

BASE_TRAVEL = {
    "IN":  {"M1": 4000, "M2": 5000, "M3": 7000, "BUF": 3000, "OUT": 8000},
    "M1":  {"M2": 4000, "M3": 6000, "BUF": 3000, "OUT": 7000},
    "M2":  {"M3": 5000, "BUF": 3000, "OUT": 6000},
    "M3":  {"BUF": 4000, "OUT": 5000},
    "BUF": {"OUT": 5000},
    "OUT": {},
}

BASE_ROBOT = {"start_pos": "IN", "pick_ms": 2000, "place_ms": 2000}


def _make_parts(rng: random.Random, types: list[str], n: int, start_id: int = 1):
    parts = []
    for i in range(n):
        t = types[rng.randint(0, len(types) - 1)]
        parts.append({"id": f"P{start_id + i:03d}", "type": t})
    return parts


def _chunk_orders(parts: list, order_size: int, release_start: int,
                  interval: int) -> list[dict]:
    """부품을 order_size 단위로 균등 분할해 주문 목록 생성."""
    orders, idx, oid = [], 0, 1
    while idx < len(parts):
        chunk = parts[idx: idx + order_size]
        idx += order_size
        orders.append({"id": f"O{oid:02d}",
                       "release_ms": release_start + (oid - 1) * interval,
                       "parts": chunk})
        oid += 1
    return orders


# ── F2: 단일 병목 ─────────────────────────────────────
# B형 부품 비중 높음 → M1이 첫 공정 전담 → M1에 집중 공급해야 함
# 부품 8개, 버퍼 충분, 데드락 없이 전략 차이만 측정
def _f2_instance(seed: int) -> dict:
    rng = random.Random(seed)
    parts = _make_parts(rng, ["B", "B", "B", "A"], 8, 1)
    orders = _chunk_orders(parts, 4, 0, 90000)
    return {
        "id": f"dev-F2-{seed}",
        "family": "F2",
        "horizon_ms": 1800000,
        "machines": BASE_MACHINES,
        "part_types": BASE_PART_TYPES,
        "travel_ms": BASE_TRAVEL,
        "robot": BASE_ROBOT,
        "buffer_cap": 12,
        "setup_ms": {},
        "disruptions": [],
        "orders": orders,
    }

F2_INSTANCES = [_f2_instance(s) for s in [101, 102, 103]]


# ── F3: 고 셋업 ───────────────────────────────────────
# 셋업 시간 ≈ 가공 시간 → 같은 종류 연속 처리 vs 대기 사이의 트레이드오프
# 부품 12개, 균등 3종
SETUP_F3 = {
    "M1": {"A": {"B": 40000, "C": 35000},
           "B": {"A": 40000, "C": 38000},
           "C": {"A": 35000, "B": 38000}},
    "M2": {"A": {"B": 36000, "C": 32000},
           "B": {"A": 36000, "C": 34000},
           "C": {"A": 32000, "B": 34000}},
    "M3": {"A": {"B": 30000, "C": 28000},
           "B": {"A": 30000, "C": 29000},
           "C": {"A": 28000, "B": 29000}},
}

def _f3_instance(seed: int) -> dict:
    rng = random.Random(seed)
    # 8개 부품, 균등 섞기
    base_types = ["A", "B", "C", "A", "B", "C", "A", "B"]
    rng.shuffle(base_types)
    parts = [{"id": f"P{i+1:03d}", "type": t}
             for i, t in enumerate(base_types)]
    orders = _chunk_orders(parts, 4, 0, 90000)
    return {
        "id": f"dev-F3-{seed}",
        "family": "F3",
        "horizon_ms": 2400000,
        "machines": BASE_MACHINES,
        "part_types": BASE_PART_TYPES,
        "travel_ms": BASE_TRAVEL,
        "robot": BASE_ROBOT,
        "buffer_cap": 12,
        "setup_ms": SETUP_F3,
        "disruptions": [],
        "orders": orders,
    }

F3_INSTANCES = [_f3_instance(s) for s in [201, 202, 203]]


# ── F6: 외란 ──────────────────────────────────────────
# 머신 1~2대가 중간에 멈춤 → 대체 머신으로 전환해야 함
# 부품 12개, 정지 시간 충분히 길게
def _f6_instance(seed: int) -> dict:
    rng = random.Random(seed)
    parts = _make_parts(rng, ["A", "B", "C"], 9, 1)
    orders = _chunk_orders(parts, 3, 0, 80000)
    disruptions = []
    machines = ["M1", "M2", "M3"]
    n_dis = 1
    for _attempt in range(20):
        mid = rng.choice(machines)
        t   = rng.randint(80000, 300000)
        dur = rng.randint(120000, 200000)
        disruptions.append({"machine": mid, "start_ms": t, "duration_ms": dur})
        break
    return {
        "id": f"dev-F6-{seed}",
        "family": "F6",
        "horizon_ms": 1800000,
        "machines": BASE_MACHINES,
        "part_types": BASE_PART_TYPES,
        "travel_ms": BASE_TRAVEL,
        "robot": BASE_ROBOT,
        "buffer_cap": 12,
        "setup_ms": {},
        "disruptions": disruptions,
        "orders": orders,
    }

F6_INSTANCES = [_f6_instance(s) for s in [301, 302, 303]]


ALL_INSTANCES = F2_INSTANCES + F3_INSTANCES + F6_INSTANCES
INSTANCE_BY_ID = {inst["id"]: inst for inst in ALL_INSTANCES}

