import json, os, asyncio
from contextlib import asynccontextmanager
import psycopg2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

def get_db():
    url = os.getenv("DATABASE_URL")
    if url:
        conn = psycopg2.connect(url, sslmode='require')
    else:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST","localhost"),
            port=int(os.getenv("DB_PORT","5432")),
            dbname=os.getenv("DB_NAME","fdr_platform"),
            user=os.getenv("DB_USER","fdr_user"),
            password=os.getenv("DB_PASS","fdr_secret"))
    conn.autocommit = True
    return conn

class WSManager:
    def __init__(self): self.clients = []
    async def connect(self, ws):
        await ws.accept()
        self.clients.append(ws)
    def disconnect(self, ws):
        if ws in self.clients: self.clients.remove(ws)
    async def broadcast(self, data):
        for ws in self.clients.copy():
            try: await ws.send_json(data)
            except: self.clients.remove(ws)

manager = WSManager()

def start_mqtt():
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(client_id="fdr-api")
        def on_msg(c, u, msg):
            if msg.topic.endswith("/telemetry"):
                try: asyncio.run(manager.broadcast(json.loads(msg.payload)))
                except: pass
        client.on_message = on_msg
        host = os.getenv("MQTT_HOST","localhost")
        port = int(os.getenv("MQTT_PORT","1883"))
        client.connect(host, port, keepalive=60)
        client.subscribe("flight/#")
        client.loop_start()
        print("MQTT OK")
    except Exception as e:
        print(f"MQTT non disponible: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_mqtt()
    yield

app = FastAPI(title="FDR API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root(): return {"status":"ok"}

@app.get("/flights/stats")
def stats():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT aircraft_id, count(*) AS total_frames, min(ts) AS first_seen, max(ts) AS last_seen, max(alt_m) AS alt_max, max(spd_kt) AS spd_max, max(az) AS g_max, avg(co_ppm) AS co_avg, count(*) FILTER (WHERE lte_ok=false) AS lte_dropouts FROM flight_data GROUP BY aircraft_id")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close(); conn.close()
        return [dict(zip(cols,r)) for r in rows]
    except Exception as e:
        return {"error": str(e)}

@app.get("/flights/track/{aircraft_id}")
def track(aircraft_id: str, limit: int=500):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT ts,lat,lon,alt_m,spd_kt,az,co_ppm,rssi_dbm,lte_ok FROM flight_data WHERE aircraft_id=%s ORDER BY ts DESC LIMIT %s", (aircraft_id, limit))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close(); conn.close()
        return [dict(zip(cols,r)) for r in rows]
    except Exception as e:
        return {"error": str(e)}

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
