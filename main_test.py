import asyncio
import json
import os
import re
import threading
from enum import Enum
from typing import Dict, List, Optional

import rclpy
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from rclpy.node import Node
from std_msgs.msg import String


app = FastAPI(
    title="Smart Factory Order Server",
    description="컨베이어, 로봇팔, 터틀봇 통합 주문 서버",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class Order(BaseModel):
    tower: Optional[int] = Field(default=None, ge=0)
    red: int = Field(default=0, ge=0)
    green: int = Field(default=0, ge=0)
    blue: int = Field(default=0, ge=0)


class OrderList(BaseModel):
    tower: Optional[int] = None
    id: int
    r: int
    g: int
    b: int
    state: str


class AdminCommand(BaseModel):
    type: str


class ProcessState(str, Enum):
    SYS_IDLE = "SYS_IDLE"
    WAIT_BOX_READY = "WAIT_BOX_READY"
    COMMAND_START = "COMMAND_START"
    WAIT_CONVEYOR_STAGE1 = "WAIT_CONVEYOR_STAGE1"
    RUN_FILL_TASK = "RUN_FILL_TASK"
    WAIT_FILL_DONE = "WAIT_FILL_DONE"
    COMMAND_NEXT = "COMMAND_NEXT"
    WAIT_CONVEYOR_STAGE2 = "WAIT_CONVEYOR_STAGE2"
    RUN_PACK_TASK = "RUN_PACK_TASK"
    WAIT_PACK_DONE = "WAIT_PACK_DONE"
    COMMAND_TB_DELIVERY = "COMMAND_TB_DELIVERY"
    WAIT_TB_DELIVERY_DONE = "WAIT_TB_DELIVERY_DONE"
    SHIPMENT_DONE = "SHIPMENT_DONE"


lock = threading.RLock()

next_id = 1
queue: List[OrderList] = []
current: Optional[OrderList] = None
done: List[OrderList] = []

total: Dict[str, int] = {"red": 0, "green": 0, "blue": 0}

estop = False
process_state = ProcessState.SYS_IDLE

AUTO_BOX_READY = True

# 테스트 모드: 컨베이어/포장/배송 단계를 건너뛰고 주문이 들어오면 바로 /robot/cmd(fill)를 보냅니다.
# 라즈베리파이에서 실행 전 `export DIRECT_ROBOT_FILL_ONLY=1` 로 켜세요.
DIRECT_ROBOT_FILL_ONLY = os.getenv("DIRECT_ROBOT_FILL_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
FIXED_DELIVERY_CMD = "START"
box_ready = False
MAX_VISIBLE_ORDERS = 5

clients: set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None
ros_node: Optional["RosServerNode"] = None


class RosServerNode(Node):
    def __init__(self):
        super().__init__("order_server_node")

        self.pub_conveyor_cmd = self.create_publisher(String, "/conveyor/cmd", 10)
        self.pub_robot_cmd = self.create_publisher(String, "/robot/cmd", 10)
        self.pub_robot_stop = self.create_publisher(String, "/robot/stop", 10)
        self.pub_tb_move = self.create_publisher(String, "/tb_move", 10)
        self.pub_tower_delivery = self.create_publisher(String, "/tower_delivery", 10)

        self.sub_conveyor_status = self.create_subscription(
            String,
            "/conveyor/status",
            lambda msg: handle_conveyor_status(msg.data),
            10,
        )

        self.sub_robot_done = self.create_subscription(
            String,
            "/robot/done",
            lambda msg: handle_robot_done(msg.data),
            10,
        )

        self.sub_delivery_done = self.create_subscription(
            String,
            "/delivery_done",
            lambda msg: handle_delivery_done(msg.data),
            10,
        )


def publish_string(publisher, data: str) -> None:
    msg = String()
    msg.data = data
    publisher.publish(msg)


def trim_done_locked() -> None:
    while (1 if current else 0) + len(queue) + len(done) > MAX_VISIBLE_ORDERS:
        if not done:
            break
        done.pop(0)


def make_payload() -> dict:
    with lock:
        orders = done + ([current] if current else []) + queue

        return {
            "order": [x.model_dump(exclude_none=True) for x in orders[:MAX_VISIBLE_ORDERS]],
            "total": total.copy(),
            "estop": estop,
            "process_state": process_state.value,
            "box_ready": box_ready,
            "direct_robot_fill_only": DIRECT_ROBOT_FILL_ONLY,
        }


def notify_ws() -> None:
    if main_loop is None:
        return

    payload = make_payload()

    async def send_all():
        dead = []

        for ws in list(clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            clients.discard(ws)

    asyncio.run_coroutine_threadsafe(send_all(), main_loop)


def try_start_next_job() -> bool:
    global current, process_state, box_ready

    should_start = False

    with lock:
        if estop or current is not None or not queue:
            if current is None and not queue:
                process_state = ProcessState.SYS_IDLE
            trim_done_locked()
            return False

        current = queue.pop(0)
        current.state = "처리중"
        process_state = ProcessState.WAIT_BOX_READY

        if AUTO_BOX_READY or box_ready:
            box_ready = False
            should_start = True

        trim_done_locked()

    notify_ws()

    if should_start:
        if DIRECT_ROBOT_FILL_ONLY:
            with lock:
                order_obj = current.model_copy() if current else None
            if order_obj:
                start_fill(order_obj)
        else:
            start_stage1()

    return True


def mark_box_ready() -> bool:
    global box_ready, process_state

    should_start = False

    with lock:
        box_ready = True

        if not estop and current is not None and process_state == ProcessState.WAIT_BOX_READY:
            box_ready = False
            should_start = True

    notify_ws()

    if should_start:
        if DIRECT_ROBOT_FILL_ONLY:
            with lock:
                order_obj = current.model_copy() if current else None
            if order_obj:
                start_fill(order_obj)
        else:
            start_stage1()

    return should_start


def start_stage1() -> bool:
    global process_state

    with lock:
        if estop or current is None:
            return False

        current.state = "컨베이어 1차 이동중"
        process_state = ProcessState.COMMAND_START

    notify_ws()

    if ros_node:
        publish_string(ros_node.pub_conveyor_cmd, "START")

    with lock:
        if not estop and current is not None:
            process_state = ProcessState.WAIT_CONVEYOR_STAGE1

    notify_ws()
    return True


def start_stage2() -> bool:
    global process_state

    with lock:
        if estop or current is None:
            return False

        current.state = "컨베이어 2차 이동중"
        process_state = ProcessState.COMMAND_NEXT

    notify_ws()

    if ros_node:
        publish_string(ros_node.pub_conveyor_cmd, "NEXT")

    with lock:
        if not estop and current is not None:
            process_state = ProcessState.WAIT_CONVEYOR_STAGE2

    notify_ws()
    return True


def start_fill(order_obj: OrderList) -> bool:
    global process_state

    with lock:
        if estop or current is None or current.id != order_obj.id:
            return False

        current.state = "충진중"
        process_state = ProcessState.RUN_FILL_TASK

    notify_ws()

    if ros_node:
        publish_string(
            ros_node.pub_robot_cmd,
            json.dumps(
                {
                    **({"tower": order_obj.tower} if order_obj.tower is not None else {}),
                    "task": "fill",
                    "id": order_obj.id,
                    "red": order_obj.r,
                    "green": order_obj.g,
                    "blue": order_obj.b,
                },
                ensure_ascii=False,
            ),
        )

    with lock:
        if not estop and current is not None and current.id == order_obj.id:
            process_state = ProcessState.WAIT_FILL_DONE

    notify_ws()
    return True


def start_pack(order_obj: OrderList) -> bool:
    global process_state

    with lock:
        if estop or current is None or current.id != order_obj.id:
            return False

        current.state = "포장중"
        process_state = ProcessState.RUN_PACK_TASK

    notify_ws()

    if ros_node:
        publish_string(
            ros_node.pub_robot_cmd,
            json.dumps(
                {
                    **({"tower": order_obj.tower} if order_obj.tower is not None else {}),
                    "task": "pack",
                    "id": order_obj.id,
                    "red": order_obj.r,
                    "green": order_obj.g,
                    "blue": order_obj.b,
                },
                ensure_ascii=False,
            ),
        )

    with lock:
        if not estop and current is not None and current.id == order_obj.id:
            process_state = ProcessState.WAIT_PACK_DONE

    notify_ws()
    return True


def start_turtlebot_delivery(order_obj: OrderList) -> bool:
    global process_state

    with lock:
        if estop or current is None or current.id != order_obj.id:
            return False

        current.state = "배송중"
        process_state = ProcessState.COMMAND_TB_DELIVERY

    notify_ws()

    if ros_node:
        if order_obj.tower is not None and order_obj.tower > 0:
            publish_string(ros_node.pub_tb_move, str(order_obj.tower))
            publish_string(
                ros_node.pub_tower_delivery,
                json.dumps(
                    {
                        "tower": order_obj.tower,
                        "id": order_obj.id,
                        "order_id": order_obj.id,
                    },
                    ensure_ascii=False,
                ),
            )
        else:
            publish_string(ros_node.pub_tb_move, FIXED_DELIVERY_CMD)

    with lock:
        if not estop and current is not None and current.id == order_obj.id:
            process_state = ProcessState.WAIT_TB_DELIVERY_DONE

    notify_ws()
    return True


def handle_conveyor_status(raw: str) -> None:
    global process_state

    status = raw.strip().upper()
    action = None
    order_obj = None

    with lock:
        if estop:
            return

        if current is None:
            return

        if process_state == ProcessState.WAIT_BOX_READY and status in {
            "READY",
            "BOX_READY",
            "IDLE",
            "WAIT_START",
        }:
            action = "box_ready"

        elif process_state == ProcessState.WAIT_CONVEYOR_STAGE1:
            if status == "RUNNING_TO_1":
                current.state = "컨베이어 1차 이동중"

            elif status == "DETECTED_1":
                current.state = "1번 위치 감지"

            elif status == "WAIT_NEXT":
                process_state = ProcessState.RUN_FILL_TASK
                order_obj = current.model_copy()
                action = "fill"

        elif process_state == ProcessState.WAIT_CONVEYOR_STAGE2:
            if status == "RUNNING_TO_2":
                current.state = "컨베이어 2차 이동중"

            elif status == "DETECTED_2":
                current.state = "2번 위치 감지"

            elif status == "FINISHED":
                process_state = ProcessState.RUN_PACK_TASK
                order_obj = current.model_copy()
                action = "pack"

    notify_ws()

    if action == "box_ready":
        mark_box_ready()
    elif action == "fill" and order_obj:
        start_fill(order_obj)
    elif action == "pack" and order_obj:
        start_pack(order_obj)


def handle_robot_done(raw: str) -> None:
    global current, process_state

    text = raw.strip()
    task = None
    order_id = None

    try:
        data = json.loads(text)
        task = data.get("task")
        order_id = data.get("id", data.get("order_id"))
        task = str(task).lower() if task is not None else None
        order_id = int(order_id) if order_id is not None else None

    except Exception:
        upper = text.upper()

        if "FILL" in upper:
            task = "fill"
        elif "PACK" in upper:
            task = "pack"

        match = re.search(r"\d+", upper)
        order_id = int(match.group()) if match else None

    action = None
    order_obj = None

    with lock:
        if estop or current is None:
            return

        if order_id is not None and order_id != current.id:
            return

        if task is None:
            if process_state == ProcessState.WAIT_FILL_DONE:
                task = "fill"
            elif process_state == ProcessState.WAIT_PACK_DONE:
                task = "pack"

        if task == "fill" and process_state == ProcessState.WAIT_FILL_DONE:
            current.state = "충진 완료"

            if DIRECT_ROBOT_FILL_ONLY:
                # 컨베이어/포장/터틀봇 없이 충진이 끝나면 주문을 바로 완료 처리합니다.
                current.state = "완료"
                done.append(current)
                current = None
                process_state = ProcessState.SHIPMENT_DONE
                trim_done_locked()
                action = "start_next"
            else:
                process_state = ProcessState.COMMAND_NEXT
                action = "next"

        elif task == "pack" and process_state == ProcessState.WAIT_PACK_DONE:
            current.state = "포장 완료"
            process_state = ProcessState.COMMAND_TB_DELIVERY
            order_obj = current.model_copy()
            action = "delivery"

    notify_ws()

    if action == "next":
        start_stage2()
    elif action == "delivery" and order_obj:
        start_turtlebot_delivery(order_obj)
    elif action == "start_next":
        try_start_next_job()


def handle_delivery_done(raw: str) -> None:
    global current, process_state

    text = raw.strip()
    order_id = None

    try:
        data = json.loads(text)
        order_id = data.get("id", data.get("order_id"))
        order_id = int(order_id) if order_id is not None else None

    except Exception:
        match = re.search(r"\d+", text)
        order_id = int(match.group()) if match else None

    with lock:
        if estop or current is None:
            return

        if process_state != ProcessState.WAIT_TB_DELIVERY_DONE:
            return

        if order_id is not None and order_id != current.id:
            return

        current.state = "배송 완료"
        done.append(current)
        current = None
        process_state = ProcessState.SHIPMENT_DONE
        trim_done_locked()

    notify_ws()
    try_start_next_job()


@app.on_event("startup")
async def startup():
    global main_loop, ros_node

    main_loop = asyncio.get_running_loop()

    rclpy.init(args=None)
    ros_node = RosServerNode()
    threading.Thread(target=lambda: rclpy.spin(ros_node), daemon=True).start()


@app.on_event("shutdown")
async def shutdown():
    if ros_node:
        ros_node.destroy_node()

    if rclpy.ok():
        rclpy.shutdown()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)

    try:
        await websocket.send_json(make_payload())

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass

    finally:
        clients.discard(websocket)


@app.post("/order", tags=["Order"], summary="주문 등록")
def order(o: Order):
    global next_id

    tower = o.tower if o.tower is not None and o.tower > 0 else None

    with lock:
        total["red"] += o.red
        total["green"] += o.green
        total["blue"] += o.blue

        item = OrderList(
            tower=tower,
            id=next_id,
            r=o.red,
            g=o.green,
            b=o.blue,
            state="대기",
        )

        next_id += 1
        queue.append(item)

    notify_ws()
    started = try_start_next_job()

    return {
        "order_id": item.id,
        "started": started,
        "delivery_mode": "direct_robot_fill_only" if DIRECT_ROBOT_FILL_ONLY else ("tower" if item.tower is not None else "fixed"),
    }


@app.get("/admin/queue", tags=["Admin"], summary="주문 관리")
def admin_queue():
    return make_payload()


@app.post("/command", tags=["Admin"], summary="명령")
def command_api(c: AdminCommand):
    global estop

    cmd = c.type.strip().upper()

    if cmd in {"ESTOP", "STOP"}:
        with lock:
            estop = True

            if current is not None:
                current.state = "긴급 정지"

        if ros_node:
            publish_string(ros_node.pub_conveyor_cmd, "STOP")
            publish_string(ros_node.pub_robot_stop, "STOP")

        notify_ws()
        return {"msg": "ESTOP"}

    if cmd == "START":
        with lock:
            estop = False
            order_obj = current.model_copy() if current else None
            state = process_state

        if ros_node:
            publish_string(ros_node.pub_robot_stop, "START")

        if order_obj is None:
            started = try_start_next_job()
            return {"msg": "START", "started": started}

        if state in {
            ProcessState.WAIT_BOX_READY,
            ProcessState.COMMAND_START,
            ProcessState.WAIT_CONVEYOR_STAGE1,
        }:
            if DIRECT_ROBOT_FILL_ONLY and order_obj is not None:
                resumed = start_fill(order_obj)
            else:
                resumed = start_stage1()

        elif state in {ProcessState.RUN_FILL_TASK, ProcessState.WAIT_FILL_DONE}:
            resumed = start_fill(order_obj)

        elif state in {
            ProcessState.COMMAND_NEXT,
            ProcessState.WAIT_CONVEYOR_STAGE2,
        }:
            resumed = start_stage2()

        elif state in {ProcessState.RUN_PACK_TASK, ProcessState.WAIT_PACK_DONE}:
            resumed = start_pack(order_obj)

        elif state in {
            ProcessState.COMMAND_TB_DELIVERY,
            ProcessState.WAIT_TB_DELIVERY_DONE,
        }:
            resumed = start_turtlebot_delivery(order_obj)

        else:
            resumed = try_start_next_job()

        return {"msg": "START", "resumed": resumed}

    if cmd in {"PING", "STATUS"}:
        if ros_node:
            publish_string(ros_node.pub_conveyor_cmd, cmd)

        return {"msg": cmd}

    return {"msg": "UNKNOWN_COMMAND"}


@app.post("/box/ready", tags=["Process"], summary="박스 준비")
def box_ready_api():
    started = mark_box_ready()
    return {"started": started}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
