"""
Railway AIS GeoJSON Route & Station Loader.
Parses track geometries (LineString) and station coordinates (Point)
for Jakarta-Bandung corridors (Conventional, Whoosh, and Midpoint Station).

Pure Python, zero external dependencies.
"""

import math
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GEOJSON_DIR = REPO_ROOT / "geojson"

# Default Gateway Station Coordinates: Titik Tengah Utama (Stasiun Rendeh)
DEFAULT_GATEWAY_NAME = "Titik Tengah Utama (Stasiun Rendeh)"
DEFAULT_GATEWAY_LAT = -6.58025
DEFAULT_GATEWAY_LON = 107.24695

# Hardcoded fallback waypoints [(lat, lon)] in case files are missing
FALLBACK_ROUTES = {
    "jakarta-bandung": [
        (-6.1767, 106.8307),   # Stasiun Gambir
        (-6.2151, 106.8416),   # Stasiun Manggarai
        (-6.2374, 107.0016),   # Stasiun Bekasi
        (-6.5576, 107.4452),   # Stasiun Purwakarta
        (-6.9141, 107.6119),   # Stasiun Bandung
    ],
    "tengah-bandung": [
        (-6.58025, 107.24695), # Titik Tengah Utama (Stasiun Rendeh)
        (-6.84300, 107.49730), # Padalarang Area
        (-6.91450, 107.60250), # Stasiun Bandung
    ],
    "whoosh": [
        (-6.2464, 106.8837),   # Stasiun Halim
        (-6.3262, 106.9934),   # Stasiun Karawang
        (-6.8407, 107.4912),   # Stasiun Padalarang
        (-6.9536, 107.7128),   # Stasiun Tegalluar
    ],
}


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance between two GPS coordinates in meters."""
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2) + math.cos(phi1) * math.cos(phi2) * (
        math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes compass initial bearing from (lat1, lon1) towards (lat2, lon2) in degrees (0-360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def load_geojson_station(filepath: Optional[Path] = None) -> Dict[str, Any]:
    """Loads station name and point coordinate from stasiun_tengah.json."""
    target = filepath or (GEOJSON_DIR / "stasiun_tengah.json")
    if not target.is_file():
        return {
            "name": DEFAULT_GATEWAY_NAME,
            "lat": DEFAULT_GATEWAY_LAT,
            "lon": DEFAULT_GATEWAY_LON,
        }

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        features = data.get("features", [])
        for f in features:
            geom = f.get("geometry", {})
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    props = f.get("properties", {})
                    name = props.get("nama") or props.get("stasiun_terdekat") or DEFAULT_GATEWAY_NAME
                    return {
                        "name": f"{name} (Titik Tengah)",
                        "lat": float(coords[1]),
                        "lon": float(coords[0]),
                    }
    except Exception:
        pass

    return {
        "name": DEFAULT_GATEWAY_NAME,
        "lat": DEFAULT_GATEWAY_LAT,
        "lon": DEFAULT_GATEWAY_LON,
    }


def load_geojson_route(
    filepath: Path,
    feature_name_or_index: Optional[Any] = None,
) -> List[Tuple[float, float]]:
    """
    Parses a LineString from GeoJSON and returns ordered list of (latitude, longitude).
    Coordinates in GeoJSON are [longitude, latitude].
    """
    if not filepath.is_file():
        return []

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        features = data.get("features", [])

        selected_feature = None
        if isinstance(feature_name_or_index, int) and 0 <= feature_name_or_index < len(features):
            selected_feature = features[feature_name_or_index]
        elif isinstance(feature_name_or_index, str):
            for f in features:
                props = f.get("properties", {})
                if feature_name_or_index.lower() in props.get("name", "").lower():
                    selected_feature = f
                    break

        if selected_feature is None and features:
            for f in features:
                if f.get("geometry", {}).get("type") == "LineString":
                    selected_feature = f
                    break

        if not selected_feature:
            return []

        coords = selected_feature.get("geometry", {}).get("coordinates", [])
        # Convert [lon, lat] -> (lat, lon)
        return [(float(c[1]), float(c[0])) for c in coords if len(c) >= 2]
    except Exception:
        return []


def get_route_waypoints(
    route_name: Optional[str] = None,
    node_id: Optional[int] = None,
) -> Tuple[str, List[Tuple[float, float]]]:
    """
    Resolves track waypoints based on route name, file path, or node ID:
    - Node 0x0002 -> Conventional Jakarta - Bandung corridor
    - Node 0x0003 -> Segmen 2: Titik Tengah ke Bandung
    - Custom file or key -> Resolved dynamically
    """
    route_key = (route_name or "auto").strip().lower()

    if route_key in ("auto", ""):
        if node_id == 0x0003 or node_id == 3:
            route_key = "tengah-bandung"
        else:
            route_key = "jakarta-bandung"

    # Direct file path
    custom_path = Path(route_key)
    if custom_path.is_file():
        pts = load_geojson_route(custom_path)
        if pts:
            return custom_path.stem, pts

    # Route presets from geojson/ directory
    if "tengah" in route_key or "segmen2" in route_key:
        geo_f = GEOJSON_DIR / "tengah_bandung_ke_bandung.json"
        pts = load_geojson_route(geo_f, 0)
        if pts:
            return "Segmen 2: Titik Tengah -> Bandung", pts
        return "tengah-bandung", FALLBACK_ROUTES["tengah-bandung"]

    elif "whoosh" in route_key or "cepat" in route_key:
        geo_f = GEOJSON_DIR / "jalur_kereta_jakarta_bandung.json"
        pts = load_geojson_route(geo_f, 0)  # Feature 0 is Whoosh
        if pts:
            return "Kereta Cepat Whoosh (Halim -> Tegalluar)", pts
        return "whoosh", FALLBACK_ROUTES["whoosh"]

    else:
        # Conventional Gambir - Bandung
        geo_f = GEOJSON_DIR / "jalur_kereta_jakarta_bandung.json"
        pts = load_geojson_route(geo_f, 1)  # Feature 1 is Conventional
        if pts:
            return "Jalur Konvensional (Gambir -> Bandung)", pts
        return "jakarta-bandung", FALLBACK_ROUTES["jakarta-bandung"]
