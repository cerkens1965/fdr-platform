"""
FDR Boîtier Simulator
=====================
Reproduit exactement les trames MQTT du boîtier ESP32 en vol.
Publie sur : flight/{club_id}/{aircraft_id}/telemetry
Alertes sur : flight/{club_id}/{aircraft_id}/alert

Usage:
    python simulator.py                          # vol normal
    python simulator.py --scenario lte_failure   # coupure LTE simulée
    python simulator.py --scenario high_g        # dépassement G-force
    python simulator.py --scenario co_alert      # alerte CO cabine
    python simulator.py --scenario full          # tous les incidents
    python simulator.py --list                   # liste des scénarios

Requirements:
    pip install paho-mqtt
"""

import time
import json
import math
import random
import argparse
import threading
import signal
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("❌ Installer paho-mqtt : pip install paho-mqtt")
    sys.exit(1)

# ─────────────────────────────────────────────
# CONFIGURATION — adapter à votre environnement
# ─────────────────────────────────────────────
CONFIG = {
    # MQTT broker
    "broker_host": "localhost",       # Railway: votre hostname Railway
    "broker_port": 1883,              # TLS: 8883
    "broker_user": "fdr_device",      # Credentials Mosquitto ACL
    "broker_pass": "secret",

    # Identifiants
    "club_id":     "club_toulouse_01",
    "aircraft_id": "F-ABCD",

    # Fréquence d'émission live (1Hz comme le boîtier réel)
    "hz": 1,

    # Position de départ (aérodrome Toulouse Lasbordes LFCL)
    "start_lat": 43.5868,
    "start_lon": 1.4994,
    "start_alt": 151,  # altitude terrain en mètres
}

# Topics MQTT (structure multi-club)
TOPIC_TELEM  = f"flight/{CONFIG['club_id']}/{CONFIG['aircraft_id']}/telemetry"
TOPIC_ALERT  = f"flight/{CONFIG['club_id']}/{CONFIG['aircraft_id']}/alert"
TOPIC_STATUS = f"flight/{CONFIG['club_id']}/{CONFIG['aircraft_id']}/status"


# ─────────────────────────────────────────────
# STRUCTURE DE TRAME — identique CSV boîtier
# ─────────────────────────────────────────────
@dataclass
class Frame:
    ts:        str    # ISO8601 UTC
    lat:       float
    lon:       float
    alt_m:     float
    spd_kt:    float
    hdg:       float
    ax:        float  # G-force X
    ay:        float  # G-force Y
    az:        float  # G-force Z (normal = ~1.0)
    gx:        float  # gyro X °/s
    gy:        float  # gyro Y °/s
    gz:        float  # gyro Z °/s
    pres_hpa:  float
    temp_c:    float
    rpm:       float
    co_ppm:    float
    flarm_rx:  int    # nb trames FLARM reçues
    adsb_rx:   int    # nb trames ADS-B reçues
    # Champs étendus (non CSV, utiles pour le dashboard)
    rssi_dbm:  Optional[int] = None   # qualité LTE
    lte_ok:    bool = True
    seq:       int = 0                # numéro de séquence


# ─────────────────────────────────────────────
# MOTEUR DE VOL SIMULÉ
# ─────────────────────────────────────────────
class FlightEngine:
    """Génère des données physiquement cohérentes à chaque tick."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.t        = 0          # secondes écoulées depuis départ
        self.lat      = CONFIG["start_lat"]
        self.lon      = CONFIG["start_lon"]
        self.alt      = CONFIG["start_alt"]
        self.spd_kt   = 0.0
        self.hdg      = 50.0       # cap initial en degrés
        self.rpm      = 0.0
        self.co_ppm   = 3.0        # CO ambiant normal
        self.seq      = 0

        # Phase du vol
        # roulage → décollage → montée → croisière → descente → atterrissage
        self.phase    = "taxi"
        self.phase_t  = 0          # secondes dans la phase courante

    # ── Phases de vol ──────────────────────────────────────────
    PHASES = {
        "taxi":      {"duration": 60,   "spd_target": 15,   "alt_rate": 0,    "rpm_target": 1200},
        "takeoff":   {"duration": 30,   "spd_target": 75,   "alt_rate": 5,    "rpm_target": 2550},
        "climb":     {"duration": 300,  "spd_target": 90,   "alt_rate": 3.5,  "rpm_target": 2400},
        "cruise":    {"duration": 1800, "spd_target": 108,  "alt_rate": 0,    "rpm_target": 2200},
        "descent":   {"duration": 360,  "spd_target": 85,   "alt_rate": -2.5, "rpm_target": 1800},
        "landing":   {"duration": 60,   "spd_target": 0,    "alt_rate": -1,   "rpm_target": 1000},
    }
    PHASE_ORDER = ["taxi", "takeoff", "climb", "cruise", "descent", "landing"]

    def next_phase(self):
        idx = self.PHASE_ORDER.index(self.phase)
        if idx + 1 < len(self.PHASE_ORDER):
            self.phase   = self.PHASE_ORDER[idx + 1]
            self.phase_t = 0
            print(f"  ✈  Phase → {self.phase.upper()}")

    def lerp(self, current, target, rate=0.05):
        return current + (target - current) * rate

    # ── Tick principal ─────────────────────────────────────────
    def tick(self) -> Frame:
        self.t        += 1
        self.phase_t  += 1
        self.seq      += 1

        p = self.PHASES[self.phase]

        # Avancement de phase
        if self.phase_t >= p["duration"]:
            self.next_phase()
            p = self.PHASES[self.phase]

        # Vitesse et RPM
        self.spd_kt = self.lerp(self.spd_kt, p["spd_target"] + random.gauss(0, 1.5), 0.08)
        self.spd_kt = max(0, self.spd_kt)
        self.rpm    = self.lerp(self.rpm, p["rpm_target"] + random.gauss(0, 20), 0.06)

        # Altitude
        alt_rate  = p["alt_rate"] + random.gauss(0, 0.3)
        self.alt  = max(CONFIG["start_alt"], self.alt + alt_rate)
        if self.phase == "landing":
            self.alt = max(CONFIG["start_alt"], self.alt)

        # Cap (légère dérive en croisière)
        if self.phase == "cruise":
            self.hdg += random.gauss(0, 0.8)
        self.hdg = self.hdg % 360

        # Position GPS
        hdg_rad   = math.radians(self.hdg)
        spd_ms    = self.spd_kt * 0.514444
        self.lat += (spd_ms * math.cos(hdg_rad)) / 111320
        self.lon += (spd_ms * math.sin(hdg_rad)) / (111320 * math.cos(math.radians(self.lat)))

        # IMU — G-forces nominaux
        az = 1.0 + random.gauss(0, 0.02)   # charge normale
        ax = random.gauss(0, 0.03)
        ay = random.gauss(0, 0.03)
        gx = random.gauss(0, 1.0)
        gy = random.gauss(0, 1.0)
        gz = random.gauss(0, 0.5)

        # Pression / temp
        pres = 1013.25 * math.exp(-self.alt / 8500)
        temp = 15.0 - (self.alt / 1000) * 6.5 + random.gauss(0, 0.3)

        # CO nominal
        self.co_ppm = max(2, self.co_ppm + random.gauss(0, 0.1))

        # Trafic aérien simulé
        flarm_rx = random.randint(0, 3) if self.phase in ["climb","cruise","descent"] else 0
        adsb_rx  = random.randint(0, 8) if self.phase in ["climb","cruise","descent"] else 0

        # LTE — signal se dégrade en altitude
        rssi = int(-65 - (self.alt / 100) * 1.2 + random.gauss(0, 4))
        rssi = max(-120, min(-50, rssi))
        lte_ok = True

        # ── Application des scénarios ──────────────────────────
        frame = self._apply_scenario(az, ax, ay, rssi, lte_ok)
        az, ax, ay, rssi, lte_ok = frame

        return Frame(
            ts       = datetime.now(timezone.utc).isoformat(),
            lat      = round(self.lat, 6),
            lon      = round(self.lon, 6),
            alt_m    = round(self.alt, 1),
            spd_kt   = round(self.spd_kt, 1),
            hdg      = round(self.hdg, 1),
            ax       = round(ax, 3),
            ay       = round(ay, 3),
            az       = round(az, 3),
            gx       = round(gx, 2),
            gy       = round(gy, 2),
            gz       = round(gz, 2),
            pres_hpa = round(pres, 2),
            temp_c   = round(temp, 1),
            rpm      = round(self.rpm, 0),
            co_ppm   = round(self.co_ppm, 1),
            flarm_rx = flarm_rx,
            adsb_rx  = adsb_rx,
            rssi_dbm = rssi,
            lte_ok   = lte_ok,
            seq      = self.seq,
        )

    # ── Scénarios d'incidents ──────────────────────────────────
    def _apply_scenario(self, az, ax, ay, rssi, lte_ok):
        """Injecte les anomalies selon le scénario actif."""

        # SCÉNARIO : coupure LTE (entre t=120s et t=180s)
        if self.scenario in ("lte_failure", "full"):
            if 120 <= self.t <= 180:
                lte_ok = False
                rssi   = None
                if self.t == 120:
                    print("  ⚠️  SCÉNARIO : Coupure LTE déclenchée (t=120s)")
                if self.t == 180:
                    print("  ✅  SCÉNARIO : Reconnexion LTE (t=180s)")

        # SCÉNARIO : dépassement G-force (t=200s, virage serré)
        if self.scenario in ("high_g", "full"):
            if 200 <= self.t <= 215:
                az = 2.8 + random.gauss(0, 0.1)   # +2.8G
                ax = 0.4 + random.gauss(0, 0.05)
                if self.t == 200:
                    print("  ⚠️  SCÉNARIO : G-force élevée (az=2.8G) déclenchée")

        # SCÉNARIO : alerte CO (fuite moteur simulée, t=400s)
        if self.scenario in ("co_alert", "full"):
            if self.t >= 400:
                self.co_ppm = min(50, self.co_ppm + 0.3)
                if self.t == 400:
                    print("  ⚠️  SCÉNARIO : Montée CO cabine déclenchée")
                if self.t == 420:
                    print(f"  🚨  CO CRITIQUE : {self.co_ppm:.1f} ppm")

        # SCÉNARIO : vibrations moteur (t=300s, rpm instable)
        if self.scenario in ("vibration", "full"):
            if 300 <= self.t <= 330:
                self.rpm += random.gauss(0, 150)
                ax = random.gauss(0, 0.15)
                if self.t == 300:
                    print("  ⚠️  SCÉNARIO : Vibrations moteur anormales")

        return az, ax, ay, rssi, lte_ok


# ─────────────────────────────────────────────
# ALERT ENGINE — reproduit la logique embarquée
# ─────────────────────────────────────────────
class AlertEngine:
    """Détecte les dépassements de seuils et publie des alertes MQTT."""

    THRESHOLDS = {
        "g_force_z":   {"warn": 2.0,  "crit": 2.5,  "unit": "G"},
        "co_ppm":      {"warn": 20.0, "crit": 35.0, "unit": "ppm"},
        "rpm":         {"warn": 2500, "crit": 2650,  "unit": "rpm"},
        "alt_rate":    {"warn": 8.0,  "crit": 12.0,  "unit": "m/s"},
    }

    def __init__(self):
        self.prev_alt = None
        self.alerts_sent = set()  # évite le spam

    def check(self, frame: Frame) -> Optional[dict]:
        alerts = []

        # G-force
        g_total = math.sqrt(frame.ax**2 + frame.ay**2 + frame.az**2)
        if g_total > self.THRESHOLDS["g_force_z"]["crit"]:
            alerts.append({"type": "G_FORCE_CRITICAL", "value": round(g_total, 2), "unit": "G", "level": "critical"})
        elif g_total > self.THRESHOLDS["g_force_z"]["warn"]:
            alerts.append({"type": "G_FORCE_WARNING",  "value": round(g_total, 2), "unit": "G", "level": "warning"})

        # CO
        if frame.co_ppm > self.THRESHOLDS["co_ppm"]["crit"]:
            alerts.append({"type": "CO_CRITICAL", "value": frame.co_ppm, "unit": "ppm", "level": "critical"})
        elif frame.co_ppm > self.THRESHOLDS["co_ppm"]["warn"]:
            alerts.append({"type": "CO_WARNING",  "value": frame.co_ppm, "unit": "ppm", "level": "warning"})

        # RPM
        if frame.rpm > self.THRESHOLDS["rpm"]["crit"]:
            alerts.append({"type": "RPM_CRITICAL", "value": frame.rpm, "unit": "rpm", "level": "critical"})
        elif frame.rpm > self.THRESHOLDS["rpm"]["warn"]:
            alerts.append({"type": "RPM_WARNING",  "value": frame.rpm, "unit": "rpm", "level": "warning"})

        # Taux de montée/descente
        if self.prev_alt is not None:
            alt_rate = abs(frame.alt_m - self.prev_alt)  # m/s (tick = 1Hz)
            if alt_rate > self.THRESHOLDS["alt_rate"]["crit"]:
                alerts.append({"type": "ALT_RATE_CRITICAL", "value": round(alt_rate,1), "unit": "m/s", "level": "critical"})
        self.prev_alt = frame.alt_m

        # LTE perdue
        if not frame.lte_ok:
            alerts.append({"type": "LTE_LOST", "value": None, "unit": None, "level": "info"})

        return alerts if alerts else None


# ─────────────────────────────────────────────
# SIMULATEUR PRINCIPAL
# ─────────────────────────────────────────────
class FDRSimulator:

    def __init__(self, scenario: str, verbose: bool = True):
        self.scenario = scenario
        self.verbose  = verbose
        self.engine   = FlightEngine(scenario)
        self.alerts   = AlertEngine()
        self.running  = False
        self.frames_sent   = 0
        self.alerts_sent   = 0
        self.lte_dropouts  = 0

        # Client MQTT
        self.client = mqtt.Client(client_id=f"fdr-sim-{CONFIG['aircraft_id']}")
        self.client.username_pw_set(CONFIG["broker_user"], CONFIG["broker_pass"])
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish    = self._on_publish

    def _on_connect(self, client, userdata, flags, rc):
        codes = {0:"OK", 1:"Mauvaise version", 2:"Client ID rejeté", 3:"Broker indisponible", 4:"Mauvais credentials", 5:"Non autorisé"}
        if rc == 0:
            print(f"✅ Connecté au broker MQTT ({CONFIG['broker_host']}:{CONFIG['broker_port']})")
            # Publier statut "en vol"
            self.client.publish(TOPIC_STATUS, json.dumps({
                "status": "airborne",
                "aircraft": CONFIG["aircraft_id"],
                "club": CONFIG["club_id"],
                "scenario": self.scenario,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }), retain=True)
        else:
            print(f"❌ Connexion MQTT échouée : {codes.get(rc, rc)}")
            self.running = False

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            print(f"⚠️  Déconnexion inattendue (rc={rc}), tentative de reconnexion...")

    def _on_publish(self, client, userdata, mid):
        pass  # silencieux

    def connect(self) -> bool:
        try:
            self.client.connect(CONFIG["broker_host"], CONFIG["broker_port"], keepalive=60)
            self.client.loop_start()
            time.sleep(1)
            return True
        except ConnectionRefusedError:
            print(f"❌ Impossible de joindre le broker MQTT sur {CONFIG['broker_host']}:{CONFIG['broker_port']}")
            print("   → Vérifiez que Mosquitto tourne : docker compose up -d mosquitto")
            return False
        except Exception as e:
            print(f"❌ Erreur connexion : {e}")
            return False

    def run(self):
        if not self.connect():
            return

        self.running = True
        interval = 1.0 / CONFIG["hz"]

        print(f"\n{'─'*55}")
        print(f"  FDR SIMULATOR — {CONFIG['aircraft_id']} ({CONFIG['club_id']})")
        print(f"  Scénario : {self.scenario.upper()}")
        print(f"  Topic    : {TOPIC_TELEM}")
        print(f"  Fréquence: {CONFIG['hz']} Hz")
        print(f"{'─'*55}\n")

        try:
            while self.running:
                t_start = time.time()

                # Générer la trame
                frame = self.engine.tick()

                # Publier télémétrie
                payload = json.dumps(asdict(frame))
                self.client.publish(TOPIC_TELEM, payload, qos=0)
                self.frames_sent += 1

                if not frame.lte_ok:
                    self.lte_dropouts += 1

                # Vérifier les alertes
                detected = self.alerts.check(frame)
                if detected:
                    for alert in detected:
                        alert_payload = {
                            **alert,
                            "ts":         frame.ts,
                            "aircraft":   CONFIG["aircraft_id"],
                            "club":       CONFIG["club_id"],
                            "position":   {"lat": frame.lat, "lon": frame.lon, "alt": frame.alt_m},
                        }
                        self.client.publish(TOPIC_ALERT, json.dumps(alert_payload), qos=1)
                        self.alerts_sent += 1
                        if alert["level"] in ("warning", "critical"):
                            icon = "🚨" if alert["level"] == "critical" else "⚠️ "
                            print(f"  {icon} ALERTE {alert['type']:25s} val={alert['value']} {alert.get('unit','')}")

                # Affichage console (toutes les 10 trames)
                if self.verbose and self.frames_sent % 10 == 0:
                    lte_str = f"RSSI={frame.rssi_dbm}dBm" if frame.lte_ok else "LTE=OFF"
                    print(
                        f"  t={self.engine.t:5d}s  "
                        f"[{self.engine.phase:8s}]  "
                        f"alt={frame.alt_m:6.0f}m  "
                        f"spd={frame.spd_kt:5.1f}kt  "
                        f"az={frame.az:5.2f}G  "
                        f"CO={frame.co_ppm:5.1f}ppm  "
                        f"{lte_str}  "
                        f"seq={frame.seq}"
                    )

                # Maintenir la fréquence exacte
                elapsed = time.time() - t_start
                sleep_t = max(0, interval - elapsed)
                time.sleep(sleep_t)

        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self):
        self.running = False
        print(f"\n{'─'*55}")
        print(f"  SIMULATION TERMINÉE")
        print(f"  Trames envoyées : {self.frames_sent}")
        print(f"  Alertes générées: {self.alerts_sent}")
        print(f"  Coupures LTE    : {self.lte_dropouts}")
        print(f"  Durée simulée   : {self.engine.t}s ({self.engine.t//60}min {self.engine.t%60}s)")
        print(f"{'─'*55}\n")
        # Publier statut "au sol"
        self.client.publish(TOPIC_STATUS, json.dumps({
            "status": "landed",
            "aircraft": CONFIG["aircraft_id"],
            "landed_at": datetime.now(timezone.utc).isoformat(),
        }), retain=True)
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
SCENARIOS = {
    "normal":      "Vol nominal sans incident",
    "lte_failure": "Coupure LTE entre t=120s et t=180s",
    "high_g":      "Dépassement G-force à t=200s (virage serré)",
    "co_alert":    "Montée progressive CO cabine à t=400s",
    "vibration":   "Vibrations moteur anormales à t=300s",
    "full":        "Tous les incidents combinés",
}

def main():
    parser = argparse.ArgumentParser(
        description="FDR Boîtier Simulator — publie des trames MQTT identiques au boîtier réel"
    )
    parser.add_argument("--scenario", default="normal", choices=SCENARIOS.keys(),
                        help="Scénario de vol à simuler")
    parser.add_argument("--host",     default=CONFIG["broker_host"],
                        help="Adresse du broker MQTT")
    parser.add_argument("--port",     default=CONFIG["broker_port"], type=int,
                        help="Port du broker MQTT")
    parser.add_argument("--quiet",    action="store_true",
                        help="Réduire l'affichage console")
    parser.add_argument("--list",     action="store_true",
                        help="Lister les scénarios disponibles")
    args = parser.parse_args()

    if args.list:
        print("\nScénarios disponibles :")
        for name, desc in SCENARIOS.items():
            print(f"  {name:15s} — {desc}")
        print()
        return

    CONFIG["broker_host"] = args.host
    CONFIG["broker_port"] = args.port

    sim = FDRSimulator(scenario=args.scenario, verbose=not args.quiet)

    # Gestion Ctrl+C propre
    def handler(sig, frame):
        print("\n  → Arrêt demandé...")
        sim.running = False

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    sim.run()

if __name__ == "__main__":
    main()
