import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

MQTT_HOST = "localhost"
MQTT_PORT = 1883
SUBSCRIBE_TOPIC = "fleet/vehicles/+/telemetry"
DB_PATH = "telemetry.db"


# ---------- 1. Validation: what a good message must look like ----------
class Telemetry(BaseModel):
    vehicle_id: str
    trip_id: Optional[str] = None
    ts: str
    lat: float
    lng: float
    speed_kmh: float
    heading: int
    rpm: int
    coolant_temp_c: float
    fuel_pct: float
    ignition: bool
    event: Optional[str] = None


# ---------- 2. Database ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id TEXT NOT NULL,
            trip_id TEXT,
            ts TEXT NOT NULL,
            lat REAL, lng REAL,
            speed_kmh REAL, heading INTEGER, rpm INTEGER,
            coolant_temp_c REAL, fuel_pct REAL,
            ignition INTEGER,
            event TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicle_ts ON telemetry(vehicle_id, ts)")
    conn.commit()
    conn.close()


def save(t: Telemetry):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO telemetry
        (vehicle_id, trip_id, ts, lat, lng, speed_kmh, heading, rpm,
         coolant_temp_c, fuel_pct, ignition, event)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (t.vehicle_id, t.trip_id, t.ts, t.lat, t.lng, t.speed_kmh, t.heading,
         t.rpm, t.coolant_temp_c, t.fuel_pct, int(t.ignition), t.event),
    )
    conn.commit()
    conn.close()


# ---------- 3. MQTT: listen for messages ----------
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"MQTT connected ({reason_code}). session_present={flags.session_present}")
    # QoS 1 so the broker queues messages while we're offline
    client.subscribe(SUBSCRIBE_TOPIC, qos=1)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        telemetry = Telemetry(**data)
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        print(f"Rejected bad message on {msg.topic}: {e}")
        return
    save(telemetry)


# fixed client_id + clean_session=False = the broker remembers us between restarts
mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="fleet-backend",
    clean_session=False,
)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


# ---------- 4. FastAPI app ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()
    yield
    mqtt_client.loop_stop()
    mqtt_client.disconnect()


app = FastAPI(title="Fleet Tracker API", lifespan=lifespan)


@app.get("/health")
def health():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) AS n FROM telemetry").fetchone()["n"]
    conn.close()
    return {"status": "ok", "stored_messages": count}


@app.get("/vehicles/latest")
def vehicles_latest():
    """Most recent reading for each vehicle (what a live map would show)."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT t.* FROM telemetry t
        JOIN (SELECT vehicle_id, MAX(id) AS mid FROM telemetry GROUP BY vehicle_id) m
          ON t.id = m.mid
        ORDER BY t.vehicle_id
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/vehicles/{vehicle_id}/history")
def vehicle_history(vehicle_id: str, limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM telemetry WHERE vehicle_id = ? ORDER BY id DESC LIMIT ?",
        (vehicle_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/events")
def events(limit: int = 20):
    """Recent harsh braking / overspeed events across the fleet."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM telemetry WHERE event IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]