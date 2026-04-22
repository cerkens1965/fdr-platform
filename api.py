"""
FDR Platform — FastAPI Backend
Expose les données TimescaleDB en REST + WebSocket
"""

import json
import asyncio
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import paho.mqtt.client as mqtt

# ── DB ────────────────────────────────────────
DB = dict(host="localhost", port=5432, dbname="fdr_platform",
          user="fdr_user", password="fdr_secret")

def get_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    return conn

# ── WebSocket Manager ─────────────────────────
class WSManager:
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.remove(ws)

    async def broadcast(self, data: dict):
        for ws in self.clients.copy():
            try:
                await ws.send_json(data)
            except:
                self.clients.remove(ws)

manager = WSManager()

# ── MQTT → WebSocket bridge ───────────────────
def on_mqtt_message(client, userdata, msg):
    if not msg.topic.endswith("/telemetry"):
        return
    try:
        data = json.loads(msg.payload)
        asyncio.run(manager.broadcast(data))
    except:
        pass

mqtt_client = mqtt.Client(client_id="fdr-api")
mqtt_client.on_message = on_mqtt_message

@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_client.connect("localhost", 1883)
    mqtt_client.subscribe("flight/#")
    mqtt_client.loop_start()
    print("✅ FastAPI démarré — MQTT connecté")
    yield
    mqtt_client.loop_stop()

# ── App ───────────────────────────────────────
app = FastAPI(title="FDR Platform API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── Endpoints ─────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "service": "FDR Platform API"}

@app.get("/flights/stats")
def flight_stats():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            aircraft_id,
            count(*)            AS total_frames,
            min(ts)             AS first_seen,
            max(ts)             AS last_seen,
            max(alt_m)          AS alt_max,
            max(spd_kt)         AS spd_max,
            max(az)             AS g_max,
            avg(co_ppm)         AS co_avg,
            count(*) FILTER (WHERE lte_ok = false) AS lte_dropouts
        FROM flight_data
        GROUP BY aircraft_id
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close(); conn.close()
    return [dict(zip(cols, row)) for row in rows]

@app.get("/flights/track/{aircraft_id}")
def flight_track(aircraft_id: str, limit: int = 500):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ts, lat, lon, alt_m, spd_kt, az, co_ppm, rssi_dbm, lte_ok
        FROM flight_data
        WHERE aircraft_id = %s
        ORDER BY ts DESC
        LIMIT %s
    """, (aircraft_id, limit))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close(); conn.close()
    return [dict(zip(cols, row)) for row in rows]

@app.get("/flights/aggregates/{aircraft_id}")
def flight_aggregates(aircraft_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT bucket, alt_avg, alt_max, spd_avg,
               g_max, co_avg, rpm_avg, rssi_avg, lte_dropouts
        FROM flight_1min
        WHERE aircraft_id = %s
        ORDER BY bucket ASC
    """, (aircraft_id,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close(); conn.close()
    return [dict(zip(cols, row)) for row in rows]

@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)

