const wsBadge = document.getElementById("wsBadge");
const estopBadge = document.getElementById("estopBadge");

const tRed = document.getElementById("tRed");
const tGreen = document.getElementById("tGreen");
const tBlue = document.getElementById("tBlue");

const orderBody = document.getElementById("orderBody");
const debugBox = document.getElementById("debugBox");

const btnStart = document.getElementById("btnStart");
const btnEstop = document.getElementById("btnEstop");

function stateClass(s){
  if (s === "대기") return "wait";
  if (s === "처리중") return "work";
  if (s?.includes("컨베이어")) return "work";
  if (s?.includes("감지")) return "work";
  if (s?.includes("충진")) return "work";
  if (s?.includes("포장")) return "done";
  if (s === "배송중") return "ship";
  if (s === "배송 완료") return "finish";
  if (s === "긴급 정지") return "bad";
  return "";
}

function setWsBadge(ok){
  if(ok){
    wsBadge.textContent = "WS: 연결됨";
    wsBadge.className = "badge ok";
  }else{
    wsBadge.textContent = "WS: 끊김(재연결 중)";
    wsBadge.className = "badge bad";
  }
}

function setEstopBadge(estop){
  if(estop){
    estopBadge.textContent = "ESTOP: ON";
    estopBadge.className = "badge bad";
  }else{
    estopBadge.textContent = "ESTOP: OFF";
    estopBadge.className = "badge ok";
  }
}

function render(payload){
  // totals
  tRed.textContent = payload?.total?.red ?? 0;
  tGreen.textContent = payload?.total?.green ?? 0;
  tBlue.textContent = payload?.total?.blue ?? 0;

  // estop
  setEstopBadge(!!payload?.estop);

  // table (항상 5줄 고정으로 보여주고 싶으면 여기서 빈 row를 채우면 됨)
  const orders = payload?.order ?? [];
  orderBody.innerHTML = "";

  for(const o of orders){
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${o.id ?? ""}</td>
      <td>${o.tower ?? "X"}</td>
      <td>${o.r ?? 0}</td>
      <td>${o.g ?? 0}</td>
      <td>${o.b ?? 0}</td>
      <td><span class="state ${stateClass(o.state)}">${o.state ?? ""}</span></td>
    `;
    orderBody.appendChild(tr);
  }

  // debug
  debugBox.textContent = JSON.stringify(payload, null, 2);
}

async function postCommand(type){
  await fetch("/command", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({type})
  });
}

btnStart.addEventListener("click", () => postCommand("START"));
btnEstop.addEventListener("click", () => postCommand("ESTOP"));

let ws = null;
let retry = 0;

function connect(){
  const proto = (location.protocol === "https:") ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws`;

  ws = new WebSocket(url);

  ws.onopen = () => {
    retry = 0;
    setWsBadge(true);
    // 서버가 receive_text()를 기다리니까, 가끔 keepalive 보내줌
    ws.send("hello");
    setInterval(() => {
      if(ws && ws.readyState === 1) ws.send("ping");
    }, 15000);
  };

  ws.onmessage = (ev) => {
    try{
      const payload = JSON.parse(ev.data);
      render(payload);
    }catch(e){
      // ignore
    }
  };

  ws.onclose = () => {
    setWsBadge(false);
    const wait = Math.min(5000, 300 + retry * 300);
    retry += 1;
    setTimeout(connect, wait);
  };

  ws.onerror = () => {
    // close로 넘어가면서 재연결됨
  };
}

connect();
