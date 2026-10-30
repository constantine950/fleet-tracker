import json
import math
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ---------- MQTT settings ----------
MQTT_HOST = "localhost"
MQTT_PORT = 1883
TOPIC_TEMPLATE = "fleet/vehicles/{vehicle_id}/telemetry"

# ---------- Simulation settings ----------
WAYPOINTS = [
    (7.3775, 3.9470),  # Dugbe
    (7.4430, 3.9000),  # Ibadan North / UI area
    (7.3986, 3.9167),  # Mokola
    (7.3500, 3.8500),  # Apata side
    (7.4200, 3.9700),  # Challenge-ish
    (7.3300, 3.9200),  # Ring Road side
]

TICK_SECONDS = 1.0
NUM_VEHICLES = 5
MOVING_THRESHOLD_KMH = 5
OVERSPEED_LIMIT_KMH = 100
HARSH_BRAKE_DROP_KMH = 25


def haversine_km(a, b):
    """Distance in km between two (lat, lng) points."""
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def bearing_deg(a, b):
    """Compass heading from point a to point b."""
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlng = lng2 - lng1
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


class Vehicle:
    def __init__(self, vehicle_id):
        self.vehicle_id = vehicle_id
        self.pos = random.choice(WAYPOINTS)
        self.target = random.choice([w for w in WAYPOINTS if w != self.pos])
        self.speed = 0.0
        self.prev_speed = 0.0
        self.target_speed = random.uniform(30, 70)
        self.fuel = random.uniform(40, 100)
        self.coolant = 30.0
        self.ignition = True
        self.idle_ticks = 0

        # trip tracking
        self.trip_count = 0
        self.trip_id = None
        self.was_moving = False

        # bad driving
        self.overspeed_ticks = 0
        self.event = None

    def _pick_new_target(self):
        self.target = random.choice([w for w in WAYPOINTS if w != self.target])
        if self.overspeed_ticks == 0:
            self.target_speed = random.uniform(30, 70)

    def _update_trip(self):
        moving = self.speed > MOVING_THRESHOLD_KMH

        if moving and not self.was_moving:
            self.trip_count += 1
            self.trip_id = f"{self.vehicle_id}-T{self.trip_count:03d}"
        elif not moving and self.was_moving:
            self.trip_id = None

        self.was_moving = moving

    def _detect_event(self):
        drop = self.prev_speed - self.speed

        if drop >= HARSH_BRAKE_DROP_KMH:
            self.event = "harsh_brake"
        elif self.speed > OVERSPEED_LIMIT_KMH:
            self.event = "overspeed"
        else:
            self.event = None

    def step(self, dt):
        self.prev_speed = self.speed

        if self.idle_ticks > 0:
            self.idle_ticks -= 1
            self.speed = max(0.0, self.speed - 15)
        else:
            if self.overspeed_ticks > 0:
                self.overspeed_ticks -= 1
                if self.overspeed_ticks == 0:
                    self.target_speed = random.uniform(30, 70)

            diff = self.target_speed - self.speed
            self.speed += max(-12, min(8, diff)) + random.uniform(-2, 2)
            self.speed = max(0.0, self.speed)

            if self.speed > 50 and random.random() < 0.02:
                self.speed = max(10.0, self.speed - random.uniform(30, 45))
            elif self.overspeed_ticks == 0 and random.random() < 0.01:
                self.overspeed_ticks = random.randint(15, 30)
                self.target_speed = random.uniform(105, 120)

            if random.random() < 0.005:
                self.idle_ticks = random.randint(10, 30)

        dist_remaining = haversine_km(self.pos, self.target)
        step_km = (self.speed / 3600) * dt

        if step_km >= dist_remaining:
            self.pos = self.target
            self._pick_new_target()
        elif dist_remaining > 0:
            frac = step_km / dist_remaining
            self.pos = (
                self.pos[0] + (self.target[0] - self.pos[0]) * frac,
                self.pos[1] + (self.target[1] - self.pos[1]) * frac,
            )

        burn = 0.0005 + (self.speed / 100) * 0.002
        self.fuel = max(0.0, self.fuel - burn)

        target_temp = 90 if self.speed > 5 else 85
        self.coolant += (target_temp - self.coolant) * 0.02

        self._update_trip()
        self._detect_event()

    def rpm(self):
        if self.speed < 1:
            return random.randint(750, 900)
        return int(900 + self.speed * 30 + random.uniform(-100, 100))

    def payload(self):
        return {
            "vehicle_id": self.vehicle_id,
            "trip_id": self.trip_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lat": round(self.pos[0], 6),
            "lng": round(self.pos[1], 6),
            "speed_kmh": round(self.speed, 1),
            "heading": int(bearing_deg(self.pos, self.target)),
            "rpm": self.rpm(),
            "coolant_temp_c": round(self.coolant, 1),
            "fuel_pct": round(self.fuel, 1),
            "ignition": self.ignition,
            "event": self.event,
        }


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fleet-simulator")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()  # handles network stuff in the background
    return client


def main():
    client = connect_mqtt()
    fleet = [Vehicle(f"VEH-{i:03d}") for i in range(1, NUM_VEHICLES + 1)]
    print(f"Publishing {NUM_VEHICLES} vehicles to MQTT at {MQTT_HOST}:{MQTT_PORT}. Ctrl+C to stop.\n")

    try:
        while True:
            for v in fleet:
                v.step(TICK_SECONDS)
                data = v.payload()
                topic = TOPIC_TEMPLATE.format(vehicle_id=v.vehicle_id)
                client.publish(topic, json.dumps(data), qos=0)

                # only print the interesting stuff so the terminal stays readable
                if data["event"]:
                    print(f"[{data['vehicle_id']}] {data['event']} at {data['speed_kmh']} km/h")
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()