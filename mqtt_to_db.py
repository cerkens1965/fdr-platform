"""
MQTT → TimescaleDB Bridge
Subscribe aux trames du simulateur et insère en base en temps réel.
"""

import json
import signal
import sys
import psycopg2
import paho.mqtt.client as mqtt
from datetime import datetime

# ── Config base de données ────────────────────
DB = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "fdr_platform",
    "user":     "fdr_user",
    "password": "fdr_secret",
}

# ── Config MQTT ───────────────────────────────
MQTT_HOST  = "localhost"
MQTT_PORT  = 1883
MQTT_TOPIC = "flight/#"

# ── Compteurs ─────────────────────────────────
inserted = 0
errors   = 0

# ── Connexion DB ──────────────────────────────
def connect_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    print("✅ Connecté à TimescaleDB")
    return conn

conn = connect_db()
cur  = conn.cursor()

# ── Insertion d'une trame ─────────────────────
INSERT_SQL = """
INSERT INTO flight_data (
    ts, aircraft_id, club_id,
    lat, lon, alt_m, spd_kt, hdg,
    ax, ay, az, gx, gy, gz,
    pres_hpa, temp_c, rpm, co_ppm,
    flarm_rx, adsb_rx, rssi_dbm, lte_ok, seq
) VALUES (
    %(ts)s, %(aircraft_id)s, %(club_id)s,
    %(lat)s, %(lon)s, %(alt_m)s, %(spd_kt)s, %(hdg)s,
    %(ax)s, %(ay)s, %(az)s, %(gx)s, %(gy)s, %(gz)s,
    %(pres_hpa)s, %(temp_c)s, %(rpm)s, %(co_ppm)s,
    %(flarm_rx)s, %(adsb_rx)s, %(rssi_dbm)s, %(lte_ok)s, %(seq)s
)
"""

def insert_frame(data, aircraft_id, club_id):
    global inserted, errors
    try:
        data["aircraft_id"] = aircraft_id
        data["club_id"]     = club_id
        cur.execute(INSERT_SQL, data)
        inserted += 1
        if inserted % 10 == 0:
            print(f"  📥 {inserted} trames insérées — dernière: alt={data.get('alt_m')}m seq={data.get('seq')}")
    except Exception as e:
        errors += 1
        print(f"  ❌ Erreur insertion : {e}")

# ── Callbacks MQTT ────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ Connecté au broker MQTT")
        client.subscribe(MQTT_TOPIC)
        print(f"📡 Subscribe sur : {MQTT_TOPIC}")
    else:
        print(f"❌ Connexion MQTT échouée rc={rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    # Ignorer les topics status et alert pour l'instant
    if not topic.endswith("/telemetry"):
        return
    try:
        parts       = topic.split("/")
        club_id     = parts[1]
        aircraft_id = parts[2]
        data        = json.loads(msg.payload)
        insert_frame(data, aircraft_id, club_id)
    except Exception as e:
        print(f"  ⚠️  Erreur parsing : {e}")

# ── MQTT Client ───────────────────────────────
client = mqtt.Client(client_id="fdr-bridge")
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

def shutdown(sig, frame):
    print(f"\n  Bridge arrêté — {inserted} insérées, {errors} erreurs")
    client.disconnect()
    cur.close()
    conn.close()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)

print("\n─────────────────────────────────────")
print("  MQTT → TimescaleDB Bridge")
print("  En attente de trames...")
print("─────────────────────────────────────\n")

client.loop_forever()
