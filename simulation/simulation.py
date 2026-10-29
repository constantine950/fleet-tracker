import json
import math
import random
import time
from datetime import datetime, timezone

# Rough waypoints around Ibadan (lat, lng)
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
MOVING_THRESHOLD_KMH = 5     # above this speed, the car counts as "moving"
OVERSPEED_LIMIT_KMH = 100    # above this = overspeed event
HARSH_BRAKE_DROP_KMH = 25    # speed drop in one tick that counts as harsh braking


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
        self.speed = 0.0          # km/h
        self.prev_speed = 0.0     # speed on the previous tick (used to detect harsh braking)
        self.target_speed = random.uniform(30, 70)
        self.fuel = random.uniform(40, 100)  # percent
        self.coolant = 30.0       # starts cold
        self.ignition = True
        self.idle_ticks = 0       # countdown for stops

        # --- trip tracking ---
        self.trip_count = 0
        self.trip_id = None
        self.was_moving = False

        # --- bad driving ---
        self.overspeed_ticks = 0  # countdown while the driver is "flooring it"
        self.event = None         # "harsh_brake", "overspeed", or None

    def _pick_new_target(self):
        self.target = random.choice([w for w in WAYPOINTS if w != self.target])
        # don't reset the speed target while the driver is overspeeding
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
        """Look at what just happened and label it, like a real tracker would."""
        drop = self.prev_speed - self.speed

        if drop >= HARSH_BRAKE_DROP_KMH:
            self.event = "harsh_brake"
        elif self.speed > OVERSPEED_LIMIT_KMH:
            self.event = "overspeed"
        else:
            self.event = None

    def step(self, dt):
        self.prev_speed = self.speed

        # Handle idling stops (e.g. traffic, drop-offs)
        if self.idle_ticks > 0:
            self.idle_ticks -= 1
            self.speed = max(0.0, self.speed - 15)
        else:
            # Count down an overspeeding episode
            if self.overspeed_ticks > 0:
                self.overspeed_ticks -= 1
                if self.overspeed_ticks == 0:
                    self.target_speed = random.uniform(30, 70)  # calm down

            # Accelerate/decelerate toward target speed with some noise
            diff = self.target_speed - self.speed
            self.speed += max(-12, min(8, diff)) + random.uniform(-2, 2)
            self.speed = max(0.0, self.speed)

            # BAD DRIVING 1: sudden hard braking (only possible when going fast)
            if self.speed > 50 and random.random() < 0.02:
                self.speed = max(10.0, self.speed - random.uniform(30, 45))

            # BAD DRIVING 2: driver decides to speed for a while
            elif self.overspeed_ticks == 0 and random.random() < 0.01:
                self.overspeed_ticks = random.randint(15, 30)
                self.target_speed = random.uniform(105, 120)

            # Occasionally stop for a bit
            if random.random() < 0.005:
                self.idle_ticks = random.randint(10, 30)

        # Move toward target
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

        # Fuel burn: idle burns a little, driving burns more
        burn = 0.0005 + (self.speed / 100) * 0.002
        self.fuel = max(0.0, self.fuel - burn)

        # Coolant warms toward ~90C, cools slightly when slow
        target_temp = 90 if self.speed > 5 else 85
        self.coolant += (target_temp - self.coolant) * 0.02

        self._update_trip()
        self._detect_event()

    def rpm(self):
        if self.speed < 1:
            return random.randint(750, 900)  # idle
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


def main():
    fleet = [Vehicle(f"VEH-{i:03d}") for i in range(1, NUM_VEHICLES + 1)]
    print(f"Simulating {NUM_VEHICLES} vehicles. Ctrl+C to stop.\n")

    try:
        while True:
            for v in fleet:
                v.step(TICK_SECONDS)
                print(json.dumps(v.payload()))
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()