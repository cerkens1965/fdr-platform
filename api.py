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

@app.post("/setup")
def setup():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
CREATE TABLE IF NOT EXISTS flight_data (
    ts TIMESTAMPTZ NOT NULL,
    aircraft_id TEXT NOT NULL,
    club_id TEXT NOT NULL,
    lat DOUBLE PRECISION, lon DOUBLE PRECISION,
    alt_m REAL, spd_kt REAL, hdg REAL,
    ax REAL, ay REAL, az REAL,
    gx REAL, gy REAL, gz REAL,
    pres_hpa REAL, temp_c REAL,
    rpm REAL, co_ppm REAL,
    flarm_rx SMALLINT, adsb_rx SMALLINT,
    rssi_dbm SMALLINT, lte_ok BOOLEAN, seq INTEGER
)""")
        cur.close(); conn.close()
        return {"status": "table créée"}
    except Exception as e:
        return {"error": str(e)}
@app.post("/ingest")
def ingest(data: dict):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
INSERT INTO flight_data 
(ts,aircraft_id,club_id,lat,lon,alt_m,spd_kt,hdg,ax,ay,az,gx,gy,gz,pres_hpa,temp_c,rpm,co_ppm,flarm_rx,adsb_rx,rssi_dbm,lte_ok,seq)
VALUES (%(ts)s,%(aircraft_id)s,%(club_id)s,%(lat)s,%(lon)s,%(alt_m)s,%(spd_kt)s,%(hdg)s,%(ax)s,%(ay)s,%(az)s,%(gx)s,%(gy)s,%(gz)s,%(pres_hpa)s,%(temp_c)s,%(rpm)s,%(co_ppm)s,%(flarm_rx)s,%(adsb_rx)s,%(rssi_dbm)s,%(lte_ok)s,%(seq)s)
        """, data)
        cur.close(); conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}
