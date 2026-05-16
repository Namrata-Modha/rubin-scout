"""
Observatory presets for visibility planning.

Coordinates verified against official observatory websites and
the Minor Planet Center observatory list (MPC code registry).
"""

# Each entry: lat (deg N), lon (deg E), elevation_m
OBSERVATORY_PRESETS = {
    "devasthal": {
        "name": "Devasthal (ARIES/ILMT)",
        "location": "Nainital, India",
        "lat": 29.3617,
        "lon": 79.6862,
        "elevation_m": 2450,
    },
    "drao": {
        "name": "DRAO Penticton",
        "location": "British Columbia, Canada",
        "lat": 49.3210,
        "lon": -119.6205,
        "elevation_m": 545,
    },
    "dao": {
        "name": "DAO Victoria",
        "location": "British Columbia, Canada",
        "lat": 48.5219,
        "lon": -123.4172,
        "elevation_m": 74,
    },
    "maunakea": {
        "name": "Mauna Kea",
        "location": "Hawaii, USA",
        "lat": 19.8208,
        "lon": -155.4681,
        "elevation_m": 4205,
    },
    "paranal": {
        "name": "Paranal (VLT)",
        "location": "Atacama Desert, Chile",
        "lat": -24.6275,
        "lon": -70.4044,
        "elevation_m": 2635,
    },
    "lapalma": {
        "name": "La Palma (ORM)",
        "location": "Canary Islands, Spain",
        "lat": 28.7603,
        "lon": -17.8796,
        "elevation_m": 2396,
    },
    "palomar": {
        "name": "Palomar Observatory",
        "location": "California, USA",
        "lat": 33.3563,
        "lon": -116.8648,
        "elevation_m": 1712,
    },
}
