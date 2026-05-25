# mirobot_order_server

# mirobot_order_server

컨베이어, 로봇팔, 터틀봇을 연동해 주문 접수부터 충진, 포장, 배송까지 순차적으로 제어하는 Smart Factory 주문 서버입니다. FastAPI 기반 REST API와 WebSocket 대시보드를 제공하며, ROS 2 토픽을 통해 각 장비와 통신합니다.

## 주요 기능

- 주문 등록 및 주문 큐 관리
- Red / Green / Blue 품목 누적 수량 집계
- WebSocket 기반 실시간 주문 상태 모니터링
- 컨베이어 1차 이동 → 로봇팔 충진 → 컨베이어 2차 이동 → 로봇팔 포장 → 터틀봇 배송 흐름 제어
- START / ESTOP 명령 지원
- 타워 번호 기반 배송 또는 기본 배송 명령 지원
- `main_test.py`를 통한 로봇 충진 단독 테스트 모드 지원

## 프로젝트 구조

```text
mirobot_order_server/
├── main.py              # 운영용 FastAPI + ROS 2 주문 서버
├── main_test.py         # 테스트용 서버, DIRECT_ROBOT_FILL_ONLY 모드 지원
├── requirements.txt     # Python 의존성
├── templates/
│   └── index.html       # 실시간 모니터링 대시보드 화면
└── static/
    ├── app.js           # WebSocket 연결 및 화면 렌더링
    └── style.css        # 대시보드 스타일
```

## 실행 환경

- Python 3.9 이상 권장
- ROS 2 환경
- `rclpy`, `std_msgs` 사용 가능 환경
- FastAPI, Uvicorn, Pydantic

> `rclpy`는 일반적인 Python 패키지처럼 `pip`만으로 설치되지 않을 수 있습니다. 먼저 ROS 2를 설치하고 `source /opt/ros/<distro>/setup.bash`로 ROS 2 환경을 활성화한 뒤 실행하세요.

## 설치

```bash
git clone https://github.com/Jeong-Yun-Kim/mirobot_order_server.git
cd mirobot_order_server

# ROS 2 환경 활성화 예시
source /opt/ros/<ros2-distro>/setup.bash

# Python 가상환경 사용 권장
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

## 서버 실행

### 운영 서버 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

실행 후 브라우저에서 다음 주소로 접속하면 대시보드를 확인할 수 있습니다.

```text
http://localhost:8000
```

### 테스트 서버 실행

`main_test.py`는 주문이 들어오면 컨베이어, 포장, 배송 단계를 건너뛰고 바로 로봇팔 충진 명령을 보내는 테스트 모드를 지원합니다.

```bash
export DIRECT_ROBOT_FILL_ONLY=1
python3 main_test.py
```

테스트 모드를 끄려면 환경변수를 제거하거나 `0`으로 설정하세요.

```bash
unset DIRECT_ROBOT_FILL_ONLY
# 또는
export DIRECT_ROBOT_FILL_ONLY=0
```

## 대시보드

`/` 경로에서 실시간 주문 모니터링 화면을 제공합니다.

대시보드에서 확인할 수 있는 정보는 다음과 같습니다.

- WebSocket 연결 상태
- ESTOP 상태
- Red / Green / Blue 누적 주문 수량
- 최근 주문 목록, 최대 5개
- 각 주문의 타워 번호, 수량, 처리 상태
- 서버 payload 디버그 정보
- START / ESTOP 버튼

## REST API

### 주문 등록

```http
POST /order
```

#### Request Body

```json
{
  "tower": 1,
  "red": 2,
  "green": 1,
  "blue": 0
}
```

| 필드 | 타입 | 설명 |
|---|---:|---|
| `tower` | integer 또는 null | 배송할 타워 번호입니다. `0`, `null`, 미입력 시 기본 배송 모드로 처리됩니다. |
| `red` | integer | Red 품목 수량입니다. |
| `green` | integer | Green 품목 수량입니다. |
| `blue` | integer | Blue 품목 수량입니다. |

#### Response 예시

```json
{
  "order_id": 1,
  "started": true,
  "delivery_mode": "tower"
}
```

#### curl 예시

```bash
curl -X POST http://localhost:8000/order \
  -H "Content-Type: application/json" \
  -d '{"tower":1,"red":2,"green":1,"blue":0}'
```

### 주문 상태 조회

```http
GET /admin/queue
```

#### Response 예시

```json
{
  "order": [
    {
      "tower": 1,
      "id": 1,
      "r": 2,
      "g": 1,
      "b": 0,
      "state": "충진중"
    }
  ],
  "total": {
    "red": 2,
    "green": 1,
    "blue": 0
  },
  "estop": false,
  "process_state": "WAIT_FILL_DONE",
  "box_ready": false
}
```

```bash
curl http://localhost:8000/admin/queue
```

### 서버 명령

```http
POST /command
```

#### Request Body

```json
{
  "type": "ESTOP"
}
```

| 명령 | 설명 |
|---|---|
| `ESTOP` 또는 `STOP` | 긴급 정지 상태로 전환하고 컨베이어와 로봇팔에 정지 명령을 보냅니다. |
| `START` | 긴급 정지 해제 후 현재 공정 상태에 맞게 작업을 재개합니다. |
| `PING` | 컨베이어 명령 토픽으로 `PING`을 보냅니다. |
| `STATUS` | 컨베이어 명령 토픽으로 `STATUS`를 보냅니다. |

#### curl 예시

```bash
curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"type":"ESTOP"}'

curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"type":"START"}'
```

### 박스 준비 처리

```http
POST /box/ready
```

박스 준비 상태를 서버에 알립니다. 현재 서버 설정에서는 `AUTO_BOX_READY=True`로 되어 있어 주문이 들어오면 자동으로 1차 컨베이어 이동을 시작합니다.

```bash
curl -X POST http://localhost:8000/box/ready
```

## WebSocket

```text
/ws
```

서버는 주문 상태가 변경될 때마다 WebSocket 클라이언트로 현재 payload를 전송합니다. 대시보드는 이 payload를 받아 누적 수량, 주문 목록, ESTOP 상태, 공정 상태를 갱신합니다.

### Payload 예시

```json
{
  "order": [
    {
      "tower": 1,
      "id": 1,
      "r": 2,
      "g": 1,
      "b": 0,
      "state": "배송중"
    }
  ],
  "total": {
    "red": 2,
    "green": 1,
    "blue": 0
  },
  "estop": false,
  "process_state": "WAIT_TB_DELIVERY_DONE",
  "box_ready": false
}
```

`main_test.py`의 테스트 모드에서는 payload에 `direct_robot_fill_only` 필드가 추가됩니다.

## ROS 2 토픽 연동

### Publish 토픽

| 토픽 | 메시지 타입 | 설명 |
|---|---|---|
| `/conveyor/cmd` | `std_msgs/String` | 컨베이어 제어 명령을 전송합니다. 예: `START`, `NEXT`, `STOP`, `PING`, `STATUS` |
| `/robot/cmd` | `std_msgs/String` | 로봇팔 작업 명령을 JSON 문자열로 전송합니다. 예: `fill`, `pack` |
| `/robot/stop` | `std_msgs/String` | 로봇팔 정지 또는 재시작 명령을 전송합니다. 예: `STOP`, `START` |
| `/tb_move` | `std_msgs/String` | 터틀봇 이동 명령을 전송합니다. 타워 번호가 있으면 해당 번호, 없으면 `START`를 전송합니다. |
| `/tower_delivery` | `std_msgs/String` | 타워 배송 정보를 JSON 문자열로 전송합니다. |

### Subscribe 토픽

| 토픽 | 메시지 타입 | 설명 |
|---|---|---|
| `/conveyor/status` | `std_msgs/String` | 컨베이어 상태를 수신합니다. |
| `/robot/done` | `std_msgs/String` | 로봇팔 작업 완료 상태를 수신합니다. |
| `/delivery_done` | `std_msgs/String` | 배송 완료 상태를 수신합니다. |

## ROS 메시지 예시

### 컨베이어 상태 수신

서버는 `/conveyor/status`에서 다음 문자열을 처리합니다.

| 상태 | 동작 |
|---|---|
| `READY`, `BOX_READY`, `IDLE`, `WAIT_START` | 박스 준비 완료로 판단합니다. |
| `RUNNING_TO_1` | 1차 컨베이어 이동중 상태로 표시합니다. |
| `DETECTED_1` | 1번 위치 감지 상태로 표시합니다. |
| `WAIT_NEXT` | 충진 작업을 시작합니다. |
| `RUNNING_TO_2` | 2차 컨베이어 이동중 상태로 표시합니다. |
| `DETECTED_2` | 2번 위치 감지 상태로 표시합니다. |
| `FINISHED` | 포장 작업을 시작합니다. |

### 로봇팔 명령

충진 작업 예시입니다.

```json
{
  "tower": 1,
  "task": "fill",
  "id": 1,
  "red": 2,
  "green": 1,
  "blue": 0
}
```

포장 작업 예시입니다.

```json
{
  "tower": 1,
  "task": "pack",
  "id": 1,
  "red": 2,
  "green": 1,
  "blue": 0
}
```

### 로봇팔 완료 수신

`/robot/done`은 JSON 문자열 또는 `FILL`, `PACK`이 포함된 문자열을 처리할 수 있습니다.

```json
{
  "task": "fill",
  "id": 1
}
```

```json
{
  "task": "pack",
  "id": 1
}
```

### 배송 완료 수신

```json
{
  "id": 1
}
```

또는 주문 ID가 포함된 문자열도 처리할 수 있습니다.

## 공정 흐름

```text
1. /order 주문 등록
2. 주문 큐에 추가
3. 현재 작업이 없고 ESTOP 상태가 아니면 주문 처리 시작
4. 박스 준비 확인
5. /conveyor/cmd 로 START 전송
6. /conveyor/status 에서 WAIT_NEXT 수신
7. /robot/cmd 로 fill 작업 전송
8. /robot/done 에서 fill 완료 수신
9. /conveyor/cmd 로 NEXT 전송
10. /conveyor/status 에서 FINISHED 수신
11. /robot/cmd 로 pack 작업 전송
12. /robot/done 에서 pack 완료 수신
13. /tb_move 또는 /tower_delivery 로 배송 명령 전송
14. /delivery_done 수신
15. 주문 완료 처리 후 다음 주문 시작
```

## 수동 테스트 예시

서버를 실행한 뒤 다른 터미널에서 ROS 토픽을 직접 publish하여 흐름을 테스트할 수 있습니다.

```bash
# 주문 등록
curl -X POST http://localhost:8000/order \
  -H "Content-Type: application/json" \
  -d '{"tower":1,"red":1,"green":0,"blue":1}'

# 컨베이어가 1번 위치에 도착해 충진 대기 상태가 되었다고 알림
ros2 topic pub /conveyor/status std_msgs/msg/String "{data: 'WAIT_NEXT'}" --once

# 로봇팔 충진 완료
ros2 topic pub /robot/done std_msgs/msg/String "{data: '{\"task\":\"fill\",\"id\":1}'}" --once

# 컨베이어 2차 이동 완료
ros2 topic pub /conveyor/status std_msgs/msg/String "{data: 'FINISHED'}" --once

# 로봇팔 포장 완료
ros2 topic pub /robot/done std_msgs/msg/String "{data: '{\"task\":\"pack\",\"id\":1}'}" --once

# 배송 완료
ros2 topic pub /delivery_done std_msgs/msg/String "{data: '{\"id\":1}'}" --once
```

## 상태값

서버 내부 공정 상태는 다음 값을 사용합니다.

| 상태 | 의미 |
|---|---|
| `SYS_IDLE` | 대기 상태 |
| `WAIT_BOX_READY` | 박스 준비 대기 |
| `COMMAND_START` | 컨베이어 시작 명령 전송 단계 |
| `WAIT_CONVEYOR_STAGE1` | 컨베이어 1차 이동 완료 대기 |
| `RUN_FILL_TASK` | 충진 작업 시작 단계 |
| `WAIT_FILL_DONE` | 충진 완료 대기 |
| `COMMAND_NEXT` | 컨베이어 다음 이동 명령 전송 단계 |
| `WAIT_CONVEYOR_STAGE2` | 컨베이어 2차 이동 완료 대기 |
| `RUN_PACK_TASK` | 포장 작업 시작 단계 |
| `WAIT_PACK_DONE` | 포장 완료 대기 |
| `COMMAND_TB_DELIVERY` | 터틀봇 배송 명령 전송 단계 |
| `WAIT_TB_DELIVERY_DONE` | 배송 완료 대기 |
| `SHIPMENT_DONE` | 배송 완료 |

## 안전 관련 주의

`ESTOP`은 서버에서 ROS 토픽으로 정지 명령을 보내는 소프트웨어 제어입니다. 실제 장비 운용 시에는 별도의 물리적 비상정지 회로, 인터락, 안전 센서, 작업자 보호 절차를 반드시 함께 구성해야 합니다.

## 개발 메모

- 대시보드에는 최대 5개의 주문이 표시됩니다.
- 주문 수량은 서버 메모리에 저장되므로 서버를 재시작하면 초기화됩니다.
- `tower` 값이 `0`, `null`, 미입력인 경우 기본 배송 명령 `START`를 `/tb_move`로 전송합니다.
- 타워 번호가 있는 경우 `/tb_move`에는 타워 번호 문자열을 보내고, `/tower_delivery`에는 주문 ID와 타워 번호를 JSON으로 전송합니다.
- 테스트 모드인 `DIRECT_ROBOT_FILL_ONLY=1`에서는 충진 완료 후 주문을 바로 완료 처리합니다.

## License

라이선스가 아직 명시되어 있지 않습니다. 공개 배포 또는 협업을 위해 사용할 경우 `LICENSE` 파일을 추가하는 것을 권장합니다.
