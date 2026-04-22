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
            password=os.getenv("DB_PASS","fdr_secret")
        )
    conn.autocommit = True
    return conn

class WSManager:
    def __init__(self): self.clients = []
    async def connect(self, ws):
        await ws.accept(); self.clients.append(ws)
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
        print(f"✅ MQTT connecté {host}:{port}")
    except Exception as e:
        print(f"⚠️ MQTT non disponible: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_mqtt()
    yield

app = FastAPI(title="FDR Platform API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root(): return {"status":"ok","service":"FDR Platform API"}

@app.get("/flights/stats")
def stats():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT aircraft​​​​​​​​​​​​​​​​

eof
