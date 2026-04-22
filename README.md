# FDR Simulateur — Guide de démarrage

## Ce que fait ce simulateur

Reproduit **exactement** les trames MQTT du boîtier ESP32 en vol.
Publie à 1Hz sur les mêmes topics que le vrai boîtier.
Permet de tester tout le pipeline cloud sans matériel.

---

## Démarrage en 3 étapes

### Étape 1 — Lancer la stack Docker

```bash
# Créer le fichier de mots de passe Mosquitto
docker run --rm -it eclipse-mosquitto:2.0 \
  mosquitto_passwd -c -b /tmp/passwd fdr_device secret && \
  docker run --rm eclipse-mosquitto:2.0 \
  mosquitto_passwd -b /tmp/passwd fdr_backend backend_secret

# Copier le fichier généré localement
# (ou créer manuellement le fichier passwd — voir ci-dessous)

# Lancer la stack
docker compose up -d
```

**Création manuelle du fichier passwd** (plus simple) :
```bash
# Installer mosquitto-clients localement, puis :
mosquitto_passwd -c passwd fdr_device       # mot de passe: secret
mosquitto_passwd passwd fdr_backend         # mot de passe: backend_secret
mosquitto_passwd passwd fdr_dashboard       # mot de passe: dashboard_secret
mosquitto_passwd passwd fdr_simulator       # mot de passe: sim_secret
```

### Étape 2 — Installer les dépendances Python

```bash
pip install paho-mqtt
```

### Étape 3 — Lancer le simulateur

```bash
# Vol normal
python simulator.py

# Avec coupure LTE
python simulator.py --scenario lte_failure

# Dépassement G-force
python simulator.py --scenario high_g

# Alerte CO cabine
python simulator.py --scenario co_alert

# Tous les incidents combinés
python simulator.py --scenario full

# Voir tous les scénarios
python simulator.py --list
```

---

## Vérifier les données dans MQTT Explorer

1. Ouvrir http://localhost:4000
2. Se connecter à `localhost:1883`
3. User: `fdr_dashboard` / Pass: `dashboard_secret`
4. Observer les topics `flight/club_toulouse_01/F-ABCD/#`

---

## Structure des trames publiées

### Topic : `flight/{club_id}/{aircraft_id}/telemetry`
```json
{
  "ts":       "2026-04-22T14:32:01.123456+00:00",
  "lat":      43.589234,
  "lon":      1.499812,
  "alt_m":    850.3,
  "spd_kt":   96.2,
  "hdg":      52.1,
  "ax":       0.023,
  "ay":      -0.011,
  "az":       1.002,
  "gx":       0.45,
  "gy":      -0.21,
  "gz":       0.08,
  "pres_hpa": 921.4,
  "temp_c":   9.4,
  "rpm":      2198.0,
  "co_ppm":   3.2,
  "flarm_rx": 2,
  "adsb_rx":  5,
  "rssi_dbm": -78,
  "lte_ok":   true,
  "seq":      312
}
```

### Topic : `flight/{club_id}/{aircraft_id}/alert`
```json
{
  "type":     "G_FORCE_CRITICAL",
  "value":    2.85,
  "unit":     "G",
  "level":    "critical",
  "ts":       "2026-04-22T14:35:22+00:00",
  "aircraft": "F-ABCD",
  "club":     "club_toulouse_01",
  "position": {"lat": 43.612, "lon": 1.521, "alt": 1050.0}
}
```

### Topic : `flight/{club_id}/{aircraft_id}/status`
```json
{"status": "airborne", "aircraft": "F-ABCD", "started_at": "..."}
{"status": "landed",   "aircraft": "F-ABCD", "landed_at": "..."}
```

---

## Seuils d'alerte configurés

| Paramètre   | Warning    | Critical   |
|-------------|------------|------------|
| G-force (Z) | > 2.0 G    | > 2.5 G    |
| CO cabine   | > 20 ppm   | > 35 ppm   |
| RPM moteur  | > 2500 rpm | > 2650 rpm |
| Taux montée | > 8 m/s    | > 12 m/s   |

---

## Phases de vol simulées

```
TAXI (60s) → TAKEOFF (30s) → CLIMB (5min) → CRUISE (30min) → DESCENT (6min) → LANDING (60s)
```

---

## Prochaine étape : TimescaleDB

Le backend FastAPI subscribera à `flight/#` et insérera
chaque trame en base. Voir `timescale_schema.sql`.
