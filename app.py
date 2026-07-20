from flask import Flask, request, jsonify, render_template, session, redirect, make_response
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from sqlalchemy import case
import os
import re
import uuid
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import ee
import requests
import json
import random
import math
from shapely.geometry import box, shape
from flask_cors import CORS

# Load .env for localhost runs (optional — deployment platforms set real env vars)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

WEATHER_KEY = os.environ.get("WEATHER_KEY", "")
from flask_cors import CORS
import time
from urllib.parse import quote
import requests
import json
import ee
from math import sqrt
from shapely.geometry import Polygon, LineString
HEADERS = {
    "User-Agent": "GreenLens/1.0 (research project)"
}
WAQI_TOKEN = os.environ.get("WAQI_TOKEN", "") # free public token

# Overpass can be slow on home networks — default 25s locally; hosted
# deployments with a 30s request limit (e.g. Render/gunicorn) should set
# OVERPASS_TIMEOUT=8 in their environment
OVERPASS_TIMEOUT = int(os.environ.get("OVERPASS_TIMEOUT", 25))

# -----------------------------
# SIMPLE MEMORY CACHE SYSTEM
# -----------------------------
CACHE = {}


def get_cache(key):
    if key in CACHE:
        data, expiry = CACHE[key]
        if time.time() < expiry:
            print("⚡ CACHE HIT:", key)
            return data
        else:
            del CACHE[key]
    return None

def set_cache(key, value, ttl):
    CACHE[key] = (value, time.time() + ttl)



    
# 🌳 CLIMATE MATCH ENGINE

def get_ndvi_tile(n, s, e, w):

    region = ee.Geometry.Rectangle([w, s, e, n])

    image = (
        ee.ImageCollection( "COPERNICUS/S2_SR_HARMONIZED" )
        .filterBounds(region)
        .filterDate("2024-01-01", "2024-12-31")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 10))
        .median()
    )

    ndvi = image.normalizedDifference(["B8", "B4"])

    vis = {
        "min": 0,
        "max": 0.8,
        "palette": ["brown","yellow","green"]
    }

    map_id = ndvi.getMapId(vis)
    return map_id["tile_fetcher"].url_format

# 🌡 GET THERMAL HEAT TILE (Landsat)
def get_thermal_tile(north, south, east, west):

    region = ee.Geometry.Rectangle([west, south, east, north])

    collection = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate("2023-01-01", "2024-12-31")
        .sort("CLOUD_COVER")
    )

    image = collection.first()

    thermal = image.select("ST_B10").clip(region)

    thermal_c = thermal.multiply(0.00341802).add(149.0).subtract(273.15)

    vis = {
        "min": 20,
        "max": 45,
        "palette": [
            "040274",
            "2c7bb6",
            "abd9e9",
            "ffffbf",
            "fdae61",
            "d7191c"
        ]
    }

    map_id = thermal_c.getMapId(vis)
    return map_id["tile_fetcher"].url_format

# 🌳 SATELLITE PLANTATION SUITABILITY MAP
def get_plantation_tile(north, south, east, west):

    region = ee.Geometry.Rectangle([west, south, east, north])

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate("2023-01-01", "2024-12-31")
        .median()
    )

    # 🌿 NDVI vegetation
    ndvi = s2.normalizedDifference(["B8","B4"])

    # 🏙 Built-up detection (NDBI)
    ndbi = s2.normalizedDifference(["B11","B8"])

    # 💧 Water detection (NDWI)
    ndwi = s2.normalizedDifference(["B3","B8"])

    # 🌡 Heat island (simplified brightness)
    heat_proxy = s2.select("B11").gt(1200)

    # 🎯 FINAL SMART MASK
    low_veg      = ndvi.lt(0.25)      # not green
    not_water    = ndwi.lt(0.05)
    not_city     = ndbi.lt(0.25)
    warm_area    = heat_proxy

    suitable = (
        low_veg
        .And(not_water)
        .And(not_city)
        .And(warm_area)
        .focal_min(1)
        .focal_max(2)
    )

    vis = {
        "min":0,
        "max":1,
        "palette":["00000000","66ff66","00cc00","006600"]
    }

    suitable = suitable.selfMask().clip(region)
    map_id = suitable.getMapId(vis)
    return map_id["tile_fetcher"].url_format



CONTINENT_TREES = {

    "ASIA": {
        "ground": [
            "Ficus benghalensis","Azadirachta indica","Tectona grandis",
            "Terminalia arjuna","Shorea robusta","Albizia lebbeck",
            "Mangifera indica","Syzygium cumini","Dalbergia sissoo",
            "Michelia champaca"
        ],
        "rooftop": [
            "Punica granatum","Citrus limon","Psidium guajava",
            "Lagerstroemia indica","Morus alba","Prunus cerasoides",
            "Carissa carandas","Plumeria rubra","Adenium obesum",
            "Bougainvillea glabra"
        ]
    },

    "EUROPE": {
        "ground": [
            "Quercus robur","Fagus sylvatica","Acer campestre",
            "Pinus sylvestris","Betula pendula","Tilia cordata",
            "Fraxinus excelsior","Alnus glutinosa","Populus tremula",
            "Sorbus aucuparia"
        ],
        "rooftop": [
            "Prunus avium","Malus domestica","Olea europaea",
            "Camellia japonica","Corylus avellana","Viburnum tinus",
            "Amelanchier lamarckii","Crataegus monogyna",
            "Rosa rugosa","Prunus laurocerasus"
        ]
    },

    "AFRICA": {
        "ground": [
            "Adansonia digitata","Acacia senegal","Faidherbia albida",
            "Khaya senegalensis","Milicia excelsa","Afzelia africana",
            "Terminalia superba","Brachystegia spiciformis",
            "Combretum molle","Colophospermum mopane"
        ],
        "rooftop": [
            "Carissa macrocarpa","Dovyalis caffra","Olea africana",
            "Eugenia natalitia","Psidium guajava",
            "Punica granatum","Citrus sinensis",
            "Plumeria alba","Callistemon citrinus","Aloe arborescens"
        ]
    },

    "NORTH_AMERICA": {
        "ground": [
            "Quercus alba","Acer saccharum","Sequoia sempervirens",
            "Pinus ponderosa","Liquidambar styraciflua",
            "Liriodendron tulipifera","Platanus occidentalis",
            "Picea glauca","Tsuga canadensis","Fraxinus americana"
        ],
        "rooftop": [
            "Malus domestica","Prunus persica","Citrus aurantium",
            "Cercis canadensis","Ilex opaca","Cornus florida",
            "Magnolia grandiflora","Vaccinium corymbosum",
            "Juniperus virginiana","Prunus serotina"
        ]
    },

    "SOUTH_AMERICA": {
        "ground": [
            "Ceiba pentandra","Bertholletia excelsa","Swietenia macrophylla",
            "Euterpe oleracea","Tabebuia rosea","Jacaranda mimosifolia",
            "Cecropia peltata","Hymenaea courbaril",
            "Cedrela odorata","Aspidosperma polyneuron"
        ],
        "rooftop": [
            "Psidium guajava","Passiflora edulis","Citrus aurantiifolia",
            "Carica papaya","Persea americana","Annona muricata",
            "Myrciaria cauliflora","Plumeria rubra",
            "Bougainvillea spectabilis","Eugenia uniflora"
        ]
    },

    "AUSTRALIA": {
        "ground": [
            "Eucalyptus globulus","Corymbia citriodora","Acacia pycnantha",
            "Melaleuca quinquenervia","Casuarina equisetifolia",
            "Angophora costata","Grevillea robusta",
            "Araucaria cunninghamii","Banksia integrifolia",
            "Callitris columellaris"
        ],
        "rooftop": [
            "Callistemon citrinus","Lilly Pilly (Syzygium smithii)",
            "Citrus australasica","Olea europaea",
            "Prunus domestica","Macadamia integrifolia",
            "Kunzea ambigua","Leptospermum scoparium",
            "Hardenbergia violacea","Westringia fruticosa"
        ]
    },

    "ANTARCTICA": {
        "ground": [],
        "rooftop": []
    }
}


COUNTRY_CONTINENT = {
    "IN":"ASIA","CN":"ASIA","JP":"ASIA",
    "GB":"EUROPE","FR":"EUROPE","DE":"EUROPE",
    "US":"NORTH_AMERICA","CA":"NORTH_AMERICA",
    "BR":"SOUTH_AMERICA","AR":"SOUTH_AMERICA",
    "ZA":"AFRICA","KE":"AFRICA",
    "AU":"AUSTRALIA","NZ":"AUSTRALIA"
}


def get_real_aqi(lat, lon):
    # ⚡ Cache AQI for 5 minutes (data doesn't change that fast)
    key = f"aqi_{round(float(lat),2)}_{round(float(lon),2)}"
    cached = get_cache(key)
    if cached is not None:
        print("⚡ AQI CACHE HIT")
        return cached
    try:
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={WAQI_TOKEN}"
        res = requests.get(url, timeout=5).json()

        if res["status"] == "ok":
            aqi_val = res["data"]["aqi"]
            # WAQI returns "-" when a station has no data; passing that string
            # downstream crashes the scoring math ("-" / 300)
            try:
                aqi_val = int(aqi_val)
            except (TypeError, ValueError):
                return None
            set_cache(key, aqi_val, 300)
            return aqi_val

        return None

    except:
        return None

app = Flask(__name__)
CORS(app)

# EE_READY gates every satellite route — when False they return tile:None
# immediately (no per-request GEE errors) and the frontend uses OSM fallbacks
EE_READY = False
try:
    _ee_key = os.environ.get("GEE_KEY_FILE", "/etc/secrets/.ee_credentials.json")
    if os.path.exists(_ee_key):
        # Server deployment: service-account key file
        credentials = ee.ServiceAccountCredentials(
            email=os.environ.get("GEE_EMAIL", "greenlens-render@plant-project-475614.iam.gserviceaccount.com"),
            key_file=_ee_key
        )
        ee.Initialize(credentials=credentials, project=os.environ.get("GEE_PROJECT") or None)
    else:
        # Localhost: reuse credentials from `earthengine authenticate` if the
        # developer has run it; otherwise this raises and we run without GEE
        ee.Initialize(project=os.environ.get("GEE_PROJECT") or None)
    EE_READY = True
    print("✅ Earth Engine initialized successfully")
except Exception as e:
    print(f"⚠ Earth Engine not available — satellite tiles disabled, OSM-based fallbacks will be used ({type(e).__name__})")
    
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))

# ================= SECURE SESSION CONFIG =================
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# ================= BRUTE FORCE PROTECTION =================
LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS   = 5
BLOCK_MINUTES  = 15

def is_blocked(email):
    entry = LOGIN_ATTEMPTS.get(email)
    if not entry:
        return False
    if entry["attempts"] >= MAX_ATTEMPTS:
        if datetime.utcnow() < entry["blocked_until"]:
            return True
        else:
            LOGIN_ATTEMPTS.pop(email, None)
            return False
    return False

def record_failed_attempt(email):
    entry = LOGIN_ATTEMPTS.get(email, {"attempts": 0, "blocked_until": None})
    entry["attempts"] += 1
    if entry["attempts"] >= MAX_ATTEMPTS:
        entry["blocked_until"] = datetime.utcnow() + timedelta(minutes=BLOCK_MINUTES)
    LOGIN_ATTEMPTS[email] = entry

def reset_attempts(email):
    LOGIN_ATTEMPTS.pop(email, None)

def validate_password(password):
    import re
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", password):
        errors.append("At least one capital letter is required")
    if not re.search(r"[0-9]", password):
        errors.append("At least one number is required")
    if not re.search(r'[!@#$%^&*(),.?\"{}|<>]', password):
        errors.append("At least one special character is required (!@#$% etc)")
    return errors

def validate_email(email):
    import re
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w{2,}$"
    return re.match(pattern, email) is not None

# 🌍 GLOBAL CITY SEARCH (OpenWeather Geocoding)
@app.route("/search")
def search_city():
    q = request.args.get("q")

    # 1️⃣ Try OpenWeather first (works on Render with API key)
    if WEATHER_KEY:
        try:
            url = f"http://api.openweathermap.org/geo/1.0/direct?q={q}&limit=1&appid={WEATHER_KEY}"
            res = requests.get(url, timeout=6).json()
            if res and isinstance(res, list) and len(res) > 0:
                city = res[0]
                return jsonify({
                    "lat": city["lat"],
                    "lon": city["lon"],
                    "name": city["name"],
                    "country": city["country"]
                })
        except Exception as e:
            print("OpenWeather search error:", e)

    # 2️⃣ Fallback: Nominatim (free, no API key needed)
    try:
        nom_url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
        nom_res = requests.get(nom_url, headers={"User-Agent": "GreenLens/1.0"}, timeout=6).json()
        if nom_res and len(nom_res) > 0:
            place = nom_res[0]
            return jsonify({
                "lat": float(place["lat"]),
                "lon": float(place["lon"]),
                "name": place.get("display_name", q).split(",")[0],
                "country": place.get("display_name", "").split(",")[-1].strip()
            })
    except Exception as e:
        print("Nominatim search error:", e)

    return jsonify({"error": "City not found"})


# 🌆 URBAN EXPANSION CACHE LOAD
try:
    _cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "urban_cache.json")
    with open(_cache_path) as f:
        URBAN_CACHE = json.load(f)
except FileNotFoundError:
    # Fallback hardcoded cache so app starts even without the file
    URBAN_CACHE = {
        "bhopal":   {"bbox": [77.30, 23.10, 77.55, 23.35], "2019_built_percent": 32, "2024_built_percent": 41},
        "delhi":    {"bbox": [76.85, 28.40, 77.35, 28.90], "2019_built_percent": 52, "2024_built_percent": 63},
        "mumbai":   {"bbox": [72.70, 18.80, 73.05, 19.30], "2019_built_percent": 60, "2024_built_percent": 70},
        "london":   {"bbox": [-0.50, 51.30, 0.30, 51.70],  "2019_built_percent": 45, "2024_built_percent": 53},
        "nyc":      {"bbox": [-74.3, 40.45, -73.7, 40.95], "2019_built_percent": 58, "2024_built_percent": 65},
        "tokyo":    {"bbox": [139.4, 35.50, 139.9, 35.85], "2019_built_percent": 62, "2024_built_percent": 70},
        "paris":    {"bbox": [2.10,  48.75, 2.55,  48.95], "2019_built_percent": 55, "2024_built_percent": 62},
        "beijing":  {"bbox": [116.0, 39.70, 116.8, 40.15], "2019_built_percent": 50, "2024_built_percent": 60},
        "sydney":   {"bbox": [150.8, -34.1, 151.4, -33.6], "2019_built_percent": 38, "2024_built_percent": 46},
        "dubai":    {"bbox": [55.00, 25.00, 55.50, 25.40], "2019_built_percent": 48, "2024_built_percent": 58},
    }
    print("⚠ urban_cache.json not found — using built-in fallback data")

def bbox_center(b):
    return [(b[0]+b[2])/2, (b[1]+b[3])/2]

def distance(a,b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


# 🌳 LOAD CURATED TREE DATABASE


# 🌦 SIMPLE CLIMATE LOOKUP (Phase 1)
# 🌦 REAL CLIMATE LOOKUP (Open-Meteo)
def get_climate(lat, lon):

    try:
        url = (
            "https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}"
            "&start_date=2022-01-01&end_date=2022-12-31"
            "&daily=temperature_2m_mean,precipitation_sum"
            "&timezone=auto"
        )

        res = requests.get(url, timeout=10)
        if not res.ok:
            raise Exception(f"Climate API returned {res.status_code}")
        data = res.json()

        temps = data["daily"]["temperature_2m_mean"]
        rain  = data["daily"]["precipitation_sum"]

        # yearly averages
        avg_temp = round(sum(temps) / len(temps), 1)
        rainfall = round(sum(rain), 0)

        return {
            "avg_temp": avg_temp,
            "rainfall": rainfall
        }

    except Exception as e:
        print("Climate API error:", e)

        # fallback (never crash)
        return {
            "avg_temp": 27,
            "rainfall": 900
        }
    
def city_stats(name, lat, lon):

    # ⚡ Cache per city (15 minutes)
    cs_key = f"citystats_{round(float(lat),2)}_{round(float(lon),2)}"
    cached = get_cache(cs_key)
    if cached:
        return cached

    aqi = get_real_aqi(lat, lon) or 80  # AQI also cached now

    temp = None
    humidity = None
    try:
        w_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric"
        w = requests.get(w_url, timeout=6).json()
        temp = w["main"]["temp"]
        humidity = w["main"]["humidity"]
    except:
        pass

    try:
        layers = get_osm_features_bbox(lat+0.02, lat-0.02, lon+0.02, lon-0.02)
    except:
        layers = {"buildings":[],"green":[],"water":[],"industry":[],"roads":[]}

    rooftop_heat = generate_rooftop_heat(
        layers["buildings"],
        layers["industry"],
        layers["green"],
        aqi
    )

    green_score = calculate_green_score(
        aqi,
        layers["buildings"],
        layers["green"],
        layers["water"],
        rooftop_heat
    )

    city_result = {
        "name": name,
        "lat": lat,
        "lon": lon,
        "aqi": aqi,
        "temp": temp,
        "humidity": humidity,
        "green_score": green_score
    }
    set_cache(cs_key, city_result, 900)
    return city_result

# 🧠 FILTER TREES BY CLIMATE


def get_gbif_tree_species(country_code):

    key = f"trees_{country_code}"
    cached = get_cache(key)
    if cached:
        print("⚡ Tree Cache Hit:", country_code)
        return cached

    url = (
        "https://api.gbif.org/v1/species/search?"
        f"country={country_code}"
        "&rank=SPECIES"
        "&habitat=terrestrial"
        "&limit=80"
    )

    try:
        res = requests.get(url, timeout=6).json()
    except:
        return {"large": [], "small": []}

    large = []
    small = []

    large_genera = [
        "Quercus","Ficus","Cedrus","Pinus","Platanus",
        "Terminalia","Albizia","Swietenia","Acer",
        "Tectona","Sequoia","Populus"
    ]

    small_genera = [
        "Citrus","Punica","Lagerstroemia","Psidium",
        "Callistemon","Prunus","Morus","Olea",
        "Malus","Cassia"
    ]

    for s in res.get("results", []):

        name = s.get("canonicalName")
        if not name:
            continue

        genus = name.split()[0]

        if genus in large_genera and len(large) < 10:
            large.append(name)

        elif genus in small_genera and len(small) < 10:
            small.append(name)

        if len(large) >= 10 and len(small) >= 10:
            break

    result = {
        "large": large,
        "small": small
    }

    set_cache(key, result, 1800)  # 30 min cache

    return result
    
# 🌍 GET COUNTRY CODE FROM LAT/LON
def get_country_code(lat, lon):

    # 🔥 Stable cache key (rounding prevents tiny bbox changes)
    key = f"country_{round(float(lat),2)}_{round(float(lon),2)}"

    cached = get_cache(key)
    if cached:
        print("⚡ Country CACHE HIT")
        return cached

    # 🌍 Nominatim Reverse Geocode
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"

    headers = {
        "User-Agent": "GreenLens-App"
    }

    try:
        res = requests.get(url, headers=headers, timeout=5).json()
        code = res["address"]["country_code"].upper()

        # 🔥 Cache for 24 hours
        set_cache(key, code, 86400)

        return code

    except Exception as e:
        print("Country detection failed:", e)
        return "IN"   # fallback

def get_osm_features_bbox(n, s, e, w):

    # ⚡ CACHE — round to 3 decimals (~110m precision)
    cache_key = f"osm_{round(n,3)}_{round(s,3)}_{round(e,3)}_{round(w,3)}"
    cached = get_cache(cache_key)
    if cached:
        print("⚡ OSM CACHE HIT:", cache_key)
        return cached

    query = f"""
    [out:json][timeout:25];
    (
    /* ROADS */
    way["highway"]({s},{w},{n},{e});

    /* WATER MASTER */
    way["natural"="water"]({s},{w},{n},{e});
    way["waterway"]({s},{w},{n},{e});
    way["water"]({s},{w},{n},{e});
    way["landuse"="reservoir"]({s},{w},{n},{e});
    relation["natural"="water"]({s},{w},{n},{e});

    /* GREEN MASTER */
    way["leisure"="park"]({s},{w},{n},{e});
    way["landuse"="forest"]({s},{w},{n},{e});
    way["natural"="wood"]({s},{w},{n},{e});
    way["landuse"="grass"]({s},{w},{n},{e});
    way["landuse"="meadow"]({s},{w},{n},{e});
    relation["landuse"="forest"]({s},{w},{n},{e});

    /* INDUSTRY MASTER */
    way["landuse"="industrial"]({s},{w},{n},{e});
    way["landuse"="commercial"]({s},{w},{n},{e});
    way["man_made"="works"]({s},{w},{n},{e});
    way["power"="plant"]({s},{w},{n},{e});
    relation["landuse"="industrial"]({s},{w},{n},{e});

    /* BUILDINGS */
    way["building"]({s},{w},{n},{e});
    );
    out geom qt;
    """

    # ⚡ Multiple Overpass endpoints — rotate on failure (rate limit fix)
    OVERPASS_ENDPOINTS = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]

    layers = {
        "roads": [],
        "water": [],
        "green": [],
        "industry": [],
        "buildings": []
    }

    OVERPASS_HEADERS = {
        "User-Agent": "GreenLens/1.0 (environmental research; contact@greenlens.app)",
        "Accept": "application/json"
    }

    res_json = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            raw = requests.post(
                endpoint,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=OVERPASS_TIMEOUT
            )
            ct = raw.headers.get("content-type", "")
            text = raw.text.strip()

            # Must be 200 + non-empty + JSON content
            if raw.status_code == 200 and text and "json" in ct:
                res_json = raw.json()
                if "elements" in res_json:
                    print(f"✅ Overpass OK: {endpoint} ({len(res_json['elements'])} elements)")
                    break
                else:
                    print(f"⚠ Overpass {endpoint}: JSON but no 'elements' key, trying next...")

            elif raw.status_code == 200 and text and text[0] == "{":
                # JSON without correct content-type header
                try:
                    res_json = raw.json()
                    if "elements" in res_json:
                        print(f"✅ Overpass OK (no ct header): {endpoint}")
                        break
                except:
                    pass
                print(f"⚠ Overpass {endpoint}: status={raw.status_code}, ct={ct}, retrying...")

            else:
                print(f"⚠ Overpass {endpoint}: status={raw.status_code}, ct={ct}, retrying...")

            time.sleep(1)  # ✅ FIX 1: was 2

        except Exception as ep_err:
            print(f"⚠ Overpass endpoint failed ({endpoint}): {ep_err}, trying next...")
            time.sleep(1)  # ✅ FIX 1: was 2

    if res_json is None:
        print("❌ All Overpass endpoints failed — returning empty layers")
        return layers

    try:
        res = res_json

        elements = res.get("elements", [])
        for el in elements:

            # skip if geometry missing
            if "geometry" not in el:
                continue

            # collect full shape coordinates
            coords = []
            for p in el["geometry"]:
                coords.append([p["lat"], p["lon"]])

            tags = el.get("tags", {})

            # Roads → lines
            if "highway" in tags:
                layers["roads"].append(coords)

            # Water → polygons
            elif (
                tags.get("natural") == "water"
                or tags.get("water") in ["lake","pond","reservoir"]
            ):
                layers["water"].append(coords)

            # Green areas → polygons
            elif (
                tags.get("leisure") in ["park","garden","recreation_ground"]
                or tags.get("natural") in ["wood","grassland","scrub"]
                or tags.get("landuse") in ["forest","grass","meadow","orchard"]
            ):
                layers["green"].append(coords)

            # Industry → polygons
            elif (
                tags.get("landuse") in ["industrial", "commercial"]
                or tags.get("man_made") == "works"
                or tags.get("power") == "plant"
            ):
                layers["industry"].append(coords)

            # Buildings → polygons (no cap — every building in the box is kept)
            elif "building" in tags:
                layers["buildings"].append(coords)

    except Exception as e:
        print("OSM PARSE ERROR:", e)

    # 🔥 GUARANTEED STRUCTURE (never return None)
    if not layers:
        layers = {}

    layers.setdefault("roads", [])
    layers.setdefault("water", [])
    layers.setdefault("green", [])
    layers.setdefault("industry", [])
    layers.setdefault("buildings", [])

    # ⚡ Cache OSM result for 20 minutes
    set_cache(cache_key, layers, 1200)

    return layers

def clip_layers_to_bbox(layers, north, south, east, west):

    bbox = Polygon([
        (west, south),
        (east, south),
        (east, north),
        (west, north)
    ])

    clipped = {
        "roads": [],
        "water": [],
        "green": [],
        "industry": [],
        "buildings": []
    }

    for key in layers:

        for coords in layers[key]:
            try:
                # convert coords to shapely geometry
                if key == "roads":
                    geom = LineString([(p[1], p[0]) for p in coords])
                else:
                    geom = Polygon([(p[1], p[0]) for p in coords])

                if not geom.is_valid:
                    continue

                inter = geom.intersection(bbox)

                if inter.is_empty:
                    continue

                # convert back to leaflet format
                if key == "roads":
                    new_coords = [[lat, lon] for lon, lat in inter.coords]
                else:
                    new_coords = [[lat, lon] for lon, lat in list(inter.exterior.coords)]

                clipped[key].append(new_coords)

            except:
                pass

    return clipped

# ---------------------------------------------------------
# FALLBACK CITY DATABASE (FAST + STABLE)
# ---------------------------------------------------------

# 🌳 SCIENTIFIC TREE ZONE ASSIGNMENT ENGINE

def assign_species_to_zones(species_list):

    roadside = []
    parks = []
    rooftop = []
    vacant = []

    for sp in species_list:

        name = sp.lower()

        # 🛣 Roadside → pollution tolerant / hardy genera
        if any(genus in name for genus in [
            "ficus","azadirachta","polyalthia","pongamia",
            "platanus","tilia","acer","quercus","cassia"
        ]):
            roadside.append(sp)

        # 🌳 Parks → large canopy / cooling trees
        if any(genus in name for genus in [
            "delonix","jacaranda","banyan","albizia",
            "cedrus","terminalia","swietenia","sequoia"
        ]):
            parks.append(sp)

        # 🏢 Rooftop → small trees / container friendly
        if any(genus in name for genus in [
            "citrus","malus","prunus","morus",
            "lagerstroemia","olea"
        ]):
            rooftop.append(sp)

        # 🌾 Vacant land → fast growing carbon trees
        if any(genus in name for genus in [
            "eucalyptus","populus","salix",
            "leucaena","acacia","pinus"
        ]):
            vacant.append(sp)

    return {
        "roadside": roadside[:2],
        "parks": parks[:2],
        "rooftop": rooftop[:2],
        "vacant_land": vacant[:2]
    }

CITY_DB = {
    "bhopal": (23.2599, 77.4126),
    "delhi": (28.6139, 77.2090),
    "mumbai": (19.0760, 72.8777),
    "bangalore": (12.9716, 77.5946),
    "london": (51.5074, -0.1278),
    "new york": (40.7128, -74.0060),
    "tokyo": (35.6762, 139.6503),
    "paris": (48.8566, 2.3522)
}
GLOBAL_CITIES = [
    "Delhi","Mumbai","Beijing","Shanghai","Lahore","Dhaka","Karachi",
    "London","Paris","Berlin","Rome","Madrid",
    "New York","Los Angeles","Chicago","Toronto",
    "Tokyo","Seoul","Bangkok","Singapore","Dubai",
    "Sydney","Melbourne","Cape Town","Nairobi",
    "Moscow","Istanbul","Tehran","Jakarta","Manila"
]
# ======================
# DATABASE CONFIG
# ======================

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///greenlens.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

def save_upload(file_storage):
    # Unique name avoids collisions; URL built with forward slashes so it
    # works on Windows too (os.path.join gives backslashes there → 404s)
    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(file_storage.filename)}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return f"/static/uploads/{filename}"

def public_upload_url(path):
    # Normalize legacy DB values saved with os.path.join on Windows
    if not path:
        return ""
    p = str(path).replace("\\", "/")
    if not (p.startswith("/") or p.startswith("http") or p.startswith("data:")):
        p = "/" + p
    return p
# ======================
# DATABASE MODELS
# ======================

class User(db.Model):
    uid = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    avatar = db.Column(db.String, default="")
    bio = db.Column(db.String, default="Eco warrior 🌱")
    password = db.Column(db.String, nullable=True)
    email = db.Column(db.String, nullable=True, unique=True)
    is_admin = db.Column(db.Boolean, default=False)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String, nullable=False)
    author = db.Column(db.String, nullable=False)
    content = db.Column(db.String, nullable=False)
    likes = db.Column(db.Integer, default=0)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    image_url = db.Column(db.String, nullable=True)

    # ===== Repost Extension (Additive Only) =====
    original_post_id = db.Column(db.Integer, nullable=True)
    is_repost = db.Column(db.Boolean, default=False)
    shares = db.Column(db.Integer, default=0)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, nullable=False)
    uid = db.Column(db.String, nullable=False)
    text = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, nullable=False)
    uid = db.Column(db.String, nullable=False)


class Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_uid = db.Column(db.String, nullable=False)
    receiver_uid = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default="pending")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# ======================
# NOTIFICATIONS MODEL
# ======================

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receiver_uid = db.Column(db.String, nullable=False)
    sender_uid = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)
    post_id = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

# ======================
# EVENTS MODEL
# ======================

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    location = db.Column(db.String, nullable=False)
    description = db.Column(db.String, nullable=False)
    event_date = db.Column(db.DateTime, nullable=False)
    image_url = db.Column(db.String, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ======================
# MESSAGES MODEL
# ======================

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_uid = db.Column(db.String, nullable=False)
    receiver_uid = db.Column(db.String, nullable=False)
    text = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

# ======================
# INIT DB
# ======================

with app.app_context():
    db.create_all()

# ======================
# NOTIFICATION HELPER
# ======================

def create_notification(receiver, sender, ntype, post_id=None):
    if receiver == sender:
        return  # skip self-notifications

    notif = Notification(
        receiver_uid=receiver,
        sender_uid=sender,
        type=ntype,
        post_id=post_id
    )
    db.session.add(notif)
    db.session.commit()

# ======================
# PAGE ROUTES
# ======================

# Server-side gate for community pages. Client-side JS checks alone let the
# browser's back/forward cache show a protected page without re-running the
# redirect — so we block at the server AND send no-store so nothing sensitive
# is served from cache after logout. Dashboard + landing stay public.
def page_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect("/auth")
        resp = make_response(view(*args, **kwargs))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return wrapper

@app.route("/")
def home():
    return render_template("landing.html")

@app.route("/dashboard")
def dashboard():
    # Tile-layer keys are injected from env at render time (never committed to
    # git). WAQI falls back to its public "demo" token when no token is set.
    return render_template(
        "dashboard.html",
        waqi_token=WAQI_TOKEN or "demo",
        weather_key=WEATHER_KEY
    )

@app.route("/community")
@page_login_required
def social():
    return render_template("social.html")

@app.route("/auth")
def auth():
    # already logged in → skip the auth page
    if session.get("uid"):
        return redirect("/community")
    return render_template("auth.html")

@app.route("/profile.html")
@page_login_required
def profile():
    return render_template("profile.html")

@app.route("/messages.html")
@page_login_required
def messages_page():
    return render_template("messages.html")

@app.route("/inbox.html")
@page_login_required
def inbox_page():
    return render_template("inbox.html")

@app.route("/admin.html")
@page_login_required
def admin_page():
    return render_template("admin.html")

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')

# ======================
# USER APIs
# ======================

@app.route("/api/sync-user", methods=["POST"])
def sync_user():
    data = request.json
    uid = data.get("uid")
    name = data.get("name", "User")
    avatar = data.get("avatar", "")

    if not uid:
        return jsonify({"error": "uid required"}), 400

    user = User.query.get(uid)

    if not user:
        user = User(uid=uid, name=name, avatar=avatar)
        db.session.add(user)

    db.session.commit()
    return jsonify({"status": "synced"})


@app.route("/api/user/<uid>")
def get_user(uid):
    user = User.query.get(uid)

    if not user:
        return jsonify({"error": "user not found"}), 404

    return jsonify({
        "uid": user.uid,
        "name": user.name,
        "avatar": public_upload_url(user.avatar),
        "bio": user.bio,
        "email": user.email,
        "is_admin": user.is_admin
    })

@app.route("/api/notifications/delete/<int:nid>", methods=["DELETE"])
def delete_notification(nid):

    notif = Notification.query.get(nid)

    if not notif:
        return jsonify({"error": "notification not found"}), 404

    db.session.delete(notif)
    db.session.commit()

    return jsonify({"status": True})

@app.route("/api/user/update", methods=["POST"])
def update_user():
    uid = request.form.get("uid")
    name = request.form.get("name")
    bio = request.form.get("bio")
    avatar_file = request.files.get("avatar")

    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404

    if name:
        user.name = name
    if bio:
        user.bio = bio

    if avatar_file and avatar_file.filename:
        user.avatar = save_upload(avatar_file)

    db.session.commit()
    return jsonify({"status": "updated"})

# ======================
# AUTH APIs
# ======================

@app.route("/api/auth/signup", methods=["POST"])
def signup():
    data = request.json
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    # ── 1. Empty field check ──
    if not name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400

    # ── 2. Name length ──
    if len(name) < 2 or len(name) > 50:
        return jsonify({"error": "Name must be between 2 and 50 characters"}), 400

    # ── 3. Email format ──
    if not validate_email(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    # ── 4. Password strength ──
    pwd_errors = validate_password(password)
    if pwd_errors:
        return jsonify({"error": pwd_errors[0]}), 400

    # ── 5. Duplicate email ──
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "This email is already registered"}), 400

    # ── 6. Unique UID (UUID based) ──
    uid = "user-" + str(uuid.uuid4())[:8]

    # ── 7. Admin detection ──
    is_admin = (email == "admin@greenlens.com")

    user = User(
        uid=uid,
        name=name,
        email=email,
        password=generate_password_hash(password, method="pbkdf2:sha256"),
        is_admin=is_admin
    )

    db.session.add(user)
    db.session.commit()

    session.permanent = True
    session["uid"] = uid

    return jsonify({"status": "signed up", "uid": uid})


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    # ── Brute force check ──
    if is_blocked(email):
        return jsonify({"error": f"Too many failed attempts. Please try again after {BLOCK_MINUTES} minutes."}), 429

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password, password):
        record_failed_attempt(email)
        attempts_left = MAX_ATTEMPTS - LOGIN_ATTEMPTS.get(email, {}).get("attempts", 0)
        if attempts_left <= 0:
            return jsonify({"error": f"Account locked for {BLOCK_MINUTES} minutes"}), 429
        return jsonify({"error": f"Incorrect email or password ({attempts_left} attempts remaining)"}), 401

    # ── Success ──
    reset_attempts(email)
    session.permanent = True
    session["uid"] = user.uid

    return jsonify({
        "status": "logged in",
        "uid": user.uid,
        "is_admin": user.is_admin
    })

@app.route("/api/auth/logout")
def logout():
    session.pop("uid", None)
    return jsonify({"status": "logged out"})

@app.route("/api/auth/me")
def get_current_user():
    uid = session.get("uid")
    if not uid:
        return jsonify({"user": None})
    return jsonify({"user": uid})


# ======================
# CREATE EVENT (ADMIN ONLY)
# ======================

@app.route("/api/admin/events", methods=["POST"])
def create_event():

    # 1️⃣ Check login session
    uid = session.get("uid")
    if not uid:
        return jsonify({"error":"login required"}), 401

    # 2️⃣ Check admin role
    user = User.query.get(uid)
    if not user or not user.is_admin:
        return jsonify({"error":"admin only"}), 403

    # 3️⃣ Get form data
    title = request.form.get("title")
    location = request.form.get("location")
    description = request.form.get("description")
    date_str = request.form.get("date")
    image = request.files.get("image")

    if not title or not location or not description or not date_str:
        return jsonify({"error":"missing fields"}), 400

    # 4️⃣ Convert date string → datetime
    event_date = datetime.fromisoformat(date_str)

    # 5️⃣ Handle optional image upload
    image_path = None
    if image and image.filename:
        image_path = save_upload(image)

    # 6️⃣ Save event
    event = Event(
        title=title,
        location=location,
        description=description,
        event_date=event_date,
        image_url=image_path
    )

    db.session.add(event)
    db.session.commit()

    return jsonify({"status":"event created"})

# ======================
# USER SEARCH (SMART)  ← ADD HERE
# ======================

@app.route("/api/users/search/<query>/<current_uid>")
def search_users(query, current_uid):
    query_lower = query.lower()

    users = User.query.all()
    results = []

    for user in users:
        if user.uid == current_uid:
            continue

        if (
            query_lower in user.name.lower()
            or query_lower in (user.bio or "").lower()
        ):
            results.append({
                "uid": user.uid,
                "name": user.name,
                "bio": user.bio,
                "avatar": public_upload_url(user.avatar)
            })

    return jsonify(results)
# ======================
# ADMIN GET USERS
# ======================

@app.route("/api/admin/users")
def admin_get_users():

    uid = session.get("uid")
    if not uid:
        return jsonify({"error":"login required"}),401

    user = User.query.get(uid)
    if not user or not user.is_admin:
        return jsonify({"error":"admin only"}),403

    users = User.query.all()

    result = []
    for u in users:
        result.append({
            "uid":u.uid,
            "name":u.name,
            "bio":u.bio,
            "email":u.email
        })

    return jsonify(result)


# ======================
# ADMIN GET POSTS
# ======================

@app.route("/api/admin/posts")
def admin_get_posts():

    uid = session.get("uid")
    if not uid:
        return jsonify({"error":"login required"}),401

    user = User.query.get(uid)
    if not user or not user.is_admin:
        return jsonify({"error":"admin only"}),403

    posts = Post.query.order_by(Post.timestamp.desc()).all()

    result=[]

    for p in posts:
        result.append({
            "id":p.id,
            "author":p.author,
            "content":p.content,
            "likes":p.likes,
            "timestamp":p.timestamp.isoformat()
        })

    return jsonify(result)

# ======================
# ADMIN DELETE EVENT
# ======================

@app.route("/api/admin/events/delete/<int:eid>", methods=["DELETE"])
def delete_event(eid):

    uid = session.get("uid")
    if not uid:
        return jsonify({"error":"login required"}),401

    user = User.query.get(uid)

    if not user or not user.is_admin:
        return jsonify({"error":"admin only"}),403

    event = Event.query.get(eid)

    if not event:
        return jsonify({"error":"event not found"}),404

    db.session.delete(event)
    db.session.commit()

    return jsonify({"status":"deleted"})

# ======================
# ADMIN DELETE POST
# ======================

@app.route("/api/admin/posts/delete/<int:pid>", methods=["DELETE"])
def admin_delete_post(pid):

    uid = session.get("uid")
    if not uid:
        return jsonify({"error":"login required"}),401

    user = User.query.get(uid)
    if not user or not user.is_admin:
        return jsonify({"error":"admin only"}),403

    post = Post.query.get(pid)

    if not post:
        return jsonify({"error":"not found"}),404

    Comment.query.filter_by(post_id=pid).delete()
    Like.query.filter_by(post_id=pid).delete()

    db.session.delete(post)
    db.session.commit()

    return jsonify({"status":"deleted"})

# ======================
# POSTS APIs
# ======================

@app.route("/api/posts", methods=["GET"])
def get_posts():
    posts = Post.query.order_by(Post.timestamp.desc()).all()

    result = []

    for p in posts:
        post_data = {
            "id": p.id,
            "uid": p.uid,
            "author": p.author,
            "avatar": public_upload_url(User.query.get(p.uid).avatar) if User.query.get(p.uid) else "",
            "content": p.content,
            "likes": p.likes,
            "shares": p.shares,
            "timestamp": p.timestamp.isoformat(),
            "is_repost": p.is_repost,
            "original_post_id": p.original_post_id,
            "image_url": public_upload_url(p.image_url)
        }

        # If repost, attach original data
        if p.is_repost and p.original_post_id:
            original = Post.query.get(p.original_post_id)
            if original:
                post_data["original"] = {
                    "id": original.id,
                    "uid": original.uid,
                    "author": original.author,
                    "content": original.content,
                    "likes": original.likes,
                    "shares": original.shares,
                    "timestamp": original.timestamp.isoformat()
                }
            else:
                post_data["original"] = None

        result.append(post_data)
    return jsonify(result)



@app.route("/api/feed/<uid>")
def get_feed(uid):

    # Pagination params
    try:
        limit = int(request.args.get("limit", 10))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "invalid pagination params"}), 400

    # =============================
    # GET CONNECTIONS
    # =============================

    connections = Connection.query.filter(
        ((Connection.sender_uid == uid) | (Connection.receiver_uid == uid)) &
        (Connection.status == "accepted")
    ).all()

    connected_uids = set()
    for c in connections:
        other = c.receiver_uid if c.sender_uid == uid else c.sender_uid
        connected_uids.add(other)

    # =============================
    # PRIORITY ORDER
    # =============================

    priority_case = case(
        (Post.uid.in_(connected_uids), 0),
        (Post.uid == uid, 1),
        else_=2
    )

    base_query = Post.query.order_by(
        priority_case,
        Post.timestamp.desc()
    )

    total_count = base_query.count()

    posts = base_query.limit(limit).offset(offset).all()

    # =============================
    # 🚀 PERFORMANCE FIX (N+1 FIX)
    # =============================

    users = {u.uid: u for u in User.query.all()}

    result = []

    for p in posts:

        post_data = {
            "id": p.id,
            "uid": p.uid,
            "author": p.author,
            "avatar": public_upload_url(users[p.uid].avatar) if p.uid in users else "",
            "content": p.content,
            "likes": p.likes,
            "shares": p.shares,
            "timestamp": p.timestamp.isoformat(),
            "is_repost": p.is_repost,
            "image_url": public_upload_url(p.image_url),
            "original_post_id": p.original_post_id
        }

        # =============================
        # HANDLE REPOST
        # =============================

        if p.is_repost and p.original_post_id:

            original = Post.query.get(p.original_post_id)

            if original:
                post_data["original"] = {
                    "id": original.id,
                    "uid": original.uid,
                    "author": original.author,
                    "content": original.content,
                    "likes": original.likes,
                    "shares": original.shares,
                    "timestamp": original.timestamp.isoformat()
                }
            else:
                post_data["original"] = None

        result.append(post_data)

    has_more = offset + limit < total_count

    return jsonify({
        "posts": result,
        "limit": limit,
        "offset": offset,
        "has_more": has_more
    })

@app.route("/api/posts", methods=["POST"])
def create_post():
    uid = request.form.get("uid")
    content = request.form.get("content")
    image = request.files.get("image")

    if not uid:
        return jsonify({"error": "uid required"}), 400

    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "user not found"}), 400

    image_path = None

    if image and image.filename:
        image_path = save_upload(image)

    post = Post(
        uid=uid,
        author=user.name,
        content=content or "",
        image_url=image_path
    )

    db.session.add(post)
    db.session.commit()

    return jsonify({"status": "posted"})

# ======================
# EDIT POST
# ======================

@app.route("/api/posts/<int:pid>", methods=["PUT"])
def edit_post(pid):
    data = request.json
    uid = data.get("uid")
    content = data.get("content")

    post = Post.query.get(pid)
    if not post:
        return jsonify({"error": "post not found"}), 404

    if post.uid != uid:
        return jsonify({"error": "unauthorized"}), 403

    post.content = content
    db.session.commit()

    return jsonify({"status": "updated"})

# ======================
# DELETE POST
# ======================

@app.route("/api/posts/<int:pid>", methods=["DELETE"])
def delete_post(pid):
    data = request.json
    uid = data.get("uid")

    post = Post.query.get(pid)
    if not post:
        return jsonify({"error": "post not found"}), 404

    if post.uid != uid:
        return jsonify({"error": "unauthorized"}), 403

    Comment.query.filter_by(post_id=pid).delete()
    Like.query.filter_by(post_id=pid).delete()

    db.session.delete(post)
    db.session.commit()

    return jsonify({"status": "deleted"})

# ======================
# REPOST SYSTEM
# ======================

@app.route("/api/posts/repost/<int:pid>", methods=["POST"])
def repost_post(pid):
    data = request.json
    uid = data.get("uid")
    thought = data.get("thought", "").strip()

    if not uid:
        return jsonify({"error": "uid required"}), 400

    original = Post.query.get(pid)
    if not original:
        return jsonify({"error": "original post not found"}), 404

    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404

    repost = Post(
        uid=uid,
        author=user.name,
        content=thought,   # <-- user commentary
        original_post_id=original.id,
        is_repost=True
    )

    db.session.add(repost)
    original.shares += 1
    db.session.commit()

    create_notification(original.uid, uid, "repost", original.id)

    return jsonify({"status": "reposted"})

# ======================
# LIKE SYSTEM
# ======================

@app.route("/api/posts/like/<int:pid>", methods=["POST"])
def toggle_like(pid):
    data = request.json
    uid = data.get("uid")

    post = Post.query.get(pid)
    if not post:
        return jsonify({"error": "post not found"}), 404

    existing = Like.query.filter_by(post_id=pid, uid=uid).first()

    if existing:
        db.session.delete(existing)
        post.likes -= 1
    else:
        db.session.add(Like(post_id=pid, uid=uid))
        post.likes += 1

    db.session.commit()

# 🔔 notification only when new like
    if not existing:
        create_notification(post.uid, uid, "like", pid)

    return jsonify({"likes": post.likes})

# ======================
# COMMENTS SYSTEM
# ======================

@app.route("/api/comments/<int:pid>")
def get_comments(pid):
    comments = Comment.query.filter_by(post_id=pid).order_by(Comment.timestamp.asc()).all()

    result = []
    for c in comments:
        user = User.query.get(c.uid)
        result.append({
            "id": c.id,
            "uid": c.uid,
            "author": user.name if user else "User",
            "text": c.text,
            "timestamp": c.timestamp.isoformat()
        })

    return jsonify(result)


@app.route("/api/comments/<int:pid>", methods=["POST"])
def add_comment(pid):
    data = request.json
    uid = data.get("uid")
    text = data.get("text")

    if not uid or not text:
        return jsonify({"error": "missing data"}), 400

    comment = Comment(
        post_id=pid,
        uid=uid,
        text=text
    )

    db.session.add(comment)
    db.session.commit()

    post = Post.query.get(pid)
    create_notification(post.uid, uid, "comment", pid)

    return jsonify({"status": "comment added"})

# ======================
# EDIT COMMENT
# ======================

@app.route("/api/comments/<int:cid>", methods=["PUT"])
def edit_comment(cid):
    data = request.json
    uid = data.get("uid")
    text = data.get("text")

    comment = Comment.query.get(cid)
    if not comment:
        return jsonify({"error": "not found"}), 404

    if comment.uid != uid:
        return jsonify({"error": "unauthorized"}), 403

    comment.text = text
    db.session.commit()

    return jsonify({"status": "updated"})

# ======================
# DELETE COMMENT
# ======================

@app.route("/api/comments/<int:cid>", methods=["DELETE"])
def delete_comment(cid):
    data = request.json
    uid = data.get("uid")

    comment = Comment.query.get(cid)
    if not comment:
        return jsonify({"error": "not found"}), 404

    if comment.uid != uid:
        return jsonify({"error": "unauthorized"}), 403

    db.session.delete(comment)
    db.session.commit()

    return jsonify({"status": "deleted"})

# ======================
# CONNECTIONS SYSTEM (UNCHANGED)
# ======================

@app.route("/api/connections/send", methods=["POST"])
def send_request():
    data = request.json
    sender = data.get("sender")
    receiver = data.get("receiver")

    if sender == receiver:
        return jsonify({"error": "cannot connect to yourself"}), 400

    existing = Connection.query.filter(
        ((Connection.sender_uid == sender) & (Connection.receiver_uid == receiver)) |
        ((Connection.sender_uid == receiver) & (Connection.receiver_uid == sender))
    ).first()

    if existing:
        return jsonify({"status": "already exists"})

    conn = Connection(sender_uid=sender, receiver_uid=receiver)
    db.session.add(conn)
    db.session.commit()

    create_notification(receiver, sender, "connect_request")

    return jsonify({"status": "request sent"})

@app.route("/api/connections/accept", methods=["POST"])
def accept_request():
    data = request.json
    sender = data.get("sender")
    receiver = data.get("receiver")

    conn = Connection.query.filter_by(
        sender_uid=sender,
        receiver_uid=receiver,
        status="pending"
    ).first()

    if not conn:
        return jsonify({"error": "request not found"}), 404

    conn.status = "accepted"
    db.session.commit()

    create_notification(sender, receiver, "connect_accept")

    return jsonify({"status": "accepted"})

@app.route("/api/connections/<uid>")
def get_connections(uid):
    accepted = Connection.query.filter(
        ((Connection.sender_uid == uid) | (Connection.receiver_uid == uid)) &
        (Connection.status == "accepted")
    ).all()

    users = []
    for c in accepted:
        other = c.receiver_uid if c.sender_uid == uid else c.sender_uid
        user = User.query.get(other)
        if user:
            users.append({"uid": user.uid, "name": user.name})

    return jsonify(users)

@app.route("/api/connections/pending/<uid>")
def get_pending(uid):
    pending = Connection.query.filter_by(
        receiver_uid=uid,
        status="pending"
    ).all()

    result = []
    for c in pending:
        user = User.query.get(c.sender_uid)
        if user:
            result.append({"uid": user.uid, "name": user.name})

    return jsonify(result)

@app.route("/api/connections/reject", methods=["POST"])
def reject_request():
    data = request.json
    sender = data.get("sender")
    receiver = data.get("receiver")

    conn = Connection.query.filter_by(
        sender_uid=sender,
        receiver_uid=receiver,
        status="pending"
    ).first()

    if not conn:
        return jsonify({"error": "request not found"}), 404

    db.session.delete(conn)
    db.session.commit()

    return jsonify({"status": "rejected"})

@app.route("/api/connections/cancel", methods=["POST"])
def cancel_request():
    data = request.json
    sender = data.get("sender")
    receiver = data.get("receiver")

    conn = Connection.query.filter_by(
        sender_uid=sender,
        receiver_uid=receiver,
        status="pending"
    ).first()

    if not conn:
        return jsonify({"error": "request not found"}), 404

    db.session.delete(conn)
    db.session.commit()

    return jsonify({"status": "cancelled"})

@app.route("/api/connections/remove", methods=["POST"])
def remove_connection():
    data = request.json
    uid1 = data.get("uid1")
    uid2 = data.get("uid2")

    conn = Connection.query.filter(
        ((Connection.sender_uid == uid1) & (Connection.receiver_uid == uid2)) |
        ((Connection.sender_uid == uid2) & (Connection.receiver_uid == uid1))
    ).first()

    if not conn:
        return jsonify({"error": "connection not found"}), 404

    db.session.delete(conn)
    db.session.commit()

    return jsonify({"status": "removed"})

@app.route("/api/connections/count/<uid>")
def connection_count(uid):
    count = Connection.query.filter(
        ((Connection.sender_uid == uid) | (Connection.receiver_uid == uid)) &
        (Connection.status == "accepted")
    ).count()

    return jsonify({"count": count})

# ======================
# SEND MESSAGE API
# ======================

@app.route("/api/messages/send", methods=["POST"])
def send_message():
    data = request.json
    sender = data.get("sender")
    receiver = data.get("receiver")
    text = data.get("text")

    if not sender or not receiver or not text:
        return jsonify({"error": "missing data"}), 400

    msg = Message(
        sender_uid=sender,
        receiver_uid=receiver,
        text=text
    )

    db.session.add(msg)
    db.session.commit()

    return jsonify({"status": "sent"})

# ======================
# GET CONVERSATION
# ======================

@app.route("/api/messages/<uid>/<other_uid>")
def get_conversation(uid, other_uid):
    messages = Message.query.filter(
        ((Message.sender_uid == uid) & (Message.receiver_uid == other_uid)) |
        ((Message.sender_uid == other_uid) & (Message.receiver_uid == uid))
    ).order_by(Message.timestamp.asc()).all()

    result = []
    for m in messages:
        result.append({
            "id": m.id,
            "sender": m.sender_uid,
            "receiver": m.receiver_uid,
            "text": m.text,
            "timestamp": m.timestamp.isoformat()
        })

    return jsonify(result)

# ======================
# INBOX API (RECENT CHATS)
# ======================

@app.route("/api/messages/inbox/<uid>")
def get_inbox(uid):

    messages = Message.query.filter(
        (Message.sender_uid == uid) | (Message.receiver_uid == uid)
    ).order_by(Message.timestamp.desc()).all()

    conversations = {}

    for m in messages:
        other = m.receiver_uid if m.sender_uid == uid else m.sender_uid

        if other not in conversations:
            user = User.query.get(other)

            # unread messages count (other → me)
            unread = Message.query.filter_by(
                sender_uid=other,
                receiver_uid=uid,
                is_read=False
            ).count()

            conversations[other] = {
                "uid": other,
                "name": user.name if user else "User",
                "avatar": public_upload_url(user.avatar) if user else "",
                "last_message": m.text,
                "timestamp": m.timestamp.isoformat(),
                "unread": unread
            }

    return jsonify(list(conversations.values()))
# ======================
# NOTIFICATIONS API
# ======================

@app.route("/api/notifications/<uid>")
def get_notifications(uid):
    notifs = Notification.query.filter_by(
        receiver_uid=uid
    ).order_by(Notification.timestamp.desc()).all()

    result = []
    for n in notifs:
        sender = User.query.get(n.sender_uid)

        result.append({
            "id": n.id,
            "sender": sender.name if sender else "User",
            "type": n.type,
            "post_id": n.post_id,
            "timestamp": n.timestamp.isoformat(),
            "is_read": n.is_read
        })

    return jsonify(result)

# ======================
# MARK NOTIFICATIONS AS READ
# ======================

@app.route("/api/notifications/read/<uid>", methods=["POST"])
def mark_notifications_read(uid):
    Notification.query.filter_by(
        receiver_uid=uid,
        is_read=False
    ).update({"is_read": True})

    db.session.commit()
    return jsonify({"status": "marked"})

# ======================
# UNREAD COUNT
# ======================

@app.route("/api/notifications/unread/<uid>")
def unread_count(uid):
    count = Notification.query.filter_by(
        receiver_uid=uid,
        is_read=False
    ).count()

    return jsonify({"count": count})

# ======================
# CREATE EVENT (ADMIN ONLY)
# ======================


# ======================
# GET UPCOMING EVENTS
# ======================

@app.route("/api/events")
def get_events():

    now = datetime.utcnow()

    # Future events only
    events = Event.query.filter(
        Event.event_date >= now
    ).order_by(Event.event_date.asc()).limit(5).all()

    result = []
    for e in events:
        result.append({
            "id": e.id,
            "title": e.title,
            "location": e.location,
            "description": e.description,
            "date": e.event_date.isoformat(),
            "image_url": public_upload_url(e.image_url)
        })

    return jsonify(result)

@app.route("/satellite/<year>")
def satellite(year):

    if not EE_READY:
        return jsonify({"tile": None, "error": "Satellite imagery unavailable (Earth Engine not configured)"})

    year = int(year)
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    try:
        # 🛰 OLD YEARS → LANDSAT 7 + 8 COMBINE
        if year <= 2017:

            l7 = (
                ee.ImageCollection("LANDSAT/LE07/C02/T1_L2")
                .filterDate(start, end)
                .map(lambda img: img.select(
                    ["SR_B3","SR_B2","SR_B1"],   # RGB for L7
                    ["SR_B4","SR_B3","SR_B2"]
                ))
            )

            l8 = (
                ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                .filterDate(start, end)
                .map(lambda img: img.select(
                    ["SR_B4","SR_B3","SR_B2"]
                ))
            )

            image = l7.merge(l8).median()

        # 🛰 NEW YEARS → LANDSAT 8 ONLY
        else:
            image = (
                ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                .filterDate(start, end)
                .median()
            )

        vis = {
            "bands": ["SR_B4","SR_B3","SR_B2"],
            "min": 0,
            "max": 30000
        }

        map_id = image.getMapId(vis)
        tile_url = map_id["tile_fetcher"].url_format

        return jsonify({"tile": tile_url})

    except Exception as e:
        print("GEE satellite error:", e)
        return jsonify({"tile": None, "error": "Satellite imagery unavailable (Earth Engine not configured)"})


# ---------------------------------------------------------
# NDVI VEGETATION LAYER
# ---------------------------------------------------------
@app.route("/ndvi")
def ndvi():

    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    if north is None:
        return jsonify({"error":"bbox missing"}),400

    if not EE_READY:
        return jsonify({"tile": None, "error": "NDVI unavailable (Earth Engine not configured)"})

    try:
        region = ee.Geometry.Rectangle([west, south, east, north])

        # Sentinel-2 imagery
        image = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate("2023-01-01","2024-01-01")
            .median()
        )

        # NDVI calculation
        ndvi = image.normalizedDifference(["B8","B4"])

        vis = {
            "min": 0.2,
            "max": 0.9,
            "palette": [
                "#654321",  # bare soil
                "#b45309",  # dry vegetation
                "#facc15",  # low vegetation
                "#4ade80",  # medium vegetation
                "#15803d"   # dense forest
            ]
        }

        map_id = ndvi.getMapId(vis)
        tile_url = map_id["tile_fetcher"].url_format

        return jsonify({"tile": tile_url})

    except Exception as e:
        print("GEE NDVI route error:", e)
        return jsonify({"tile": None, "error": "NDVI unavailable (Earth Engine not configured)"})

# ---------------------------------------------------------
# SEARCH API
# ---------------------------------------------------------


# 🌫 REAL AQI + WEATHER FOR ANY CITY
@app.route("/city_aqi")
def city_aqi():

    lat = request.args.get("lat")
    lon = request.args.get("lon")

    # 🌫 AQI from WAQI
    aqi = get_real_aqi(lat, lon)

    # 🌡 Weather from OpenWeather
    try:
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric"
        w = requests.get(weather_url, timeout=6).json()
        return jsonify({
            "aqi": aqi,
            "temp": w["main"]["temp"],
            "humidity": w["main"]["humidity"],
            "wind": w["wind"]["speed"]
        })
    except:
        return jsonify({"aqi": aqi, "temp": None, "humidity": None, "wind": None})

# 🏘 GET CITY SUBURBS / COLONIES (OSM NOMINATIM)
@app.route("/city_suburbs")
def city_suburbs():

    lat = request.args.get("lat")
    lon = request.args.get("lon")

    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"

    try:
        data = requests.get(url, headers={"User-Agent":"GreenLens"}, timeout=6).json()
        city = data["address"].get("city") or data["address"].get("town")
        search_url = f"https://nominatim.openstreetmap.org/search?city={city}&format=json&limit=12"
        res = requests.get(search_url, headers={"User-Agent":"GreenLens"}, timeout=6).json()
        suburbs = [p["display_name"].split(",")[0] for p in res]
        return jsonify(suburbs[:10])
    except Exception as e:
        print("city_suburbs error:", e)
        return jsonify([])

@app.route("/country_cities")
def country_cities():

    lat = request.args.get("lat")
    lon = request.args.get("lon")

    headers = {"User-Agent":"GreenLens"}

    # 🌍 Get country name
    try:
        rev_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        rev = requests.get(rev_url, headers=headers, timeout=6).json()
        country = rev["address"]["country"]
    except Exception as e:
        print("country_cities nominatim error:", e)
        return jsonify([])

    # 🌆 Get top cities by population
    try:
        geo_url = "http://geodb-free-service.wirefreethought.com/v1/geo/cities?limit=100&sort=-population"
        res = requests.get(geo_url, timeout=8).json()
    except Exception as e:
        print("country_cities geodb error:", e)
        return jsonify([])

    major = []   # ✅ single consistent variable

    for c in res.get("data", []):

        geodb_country = c.get("country", "")

        if country.lower() in geodb_country.lower():
            try:
                major.append(
                    city_stats(c["city"], c["latitude"], c["longitude"])
                )
            except:
                continue

        if len(major) == 5:
            break 

    return jsonify(major)

# 🌍 SMART NEARBY AREAS (CACHED)
@app.route("/nearby_places")
def nearby_places():

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))

    cache_key = f"nearby_{round(lat,2)}_{round(lon,2)}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)

    headers = {"User-Agent":"GreenLens"}

    # 1️⃣ Reverse geocode
    country = None
    city = None
    try:
        rev_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        rev = requests.get(rev_url, headers=headers, timeout=6).json()
        address = rev.get("address", {})
        country = address.get("country")
        city = address.get("city") or address.get("town") or address.get("state")
    except Exception as e:
        print("nearby_places nominatim error:", e)

    # 2️⃣ Suburbs
    suburbs = []
    try:
        if city:
            suburb_url = f"https://nominatim.openstreetmap.org/search?city={city}&format=json&limit=6"
            suburb_res = requests.get(suburb_url, headers=headers, timeout=6).json()
            suburbs = [p["display_name"].split(",")[0] for p in suburb_res]
    except Exception as e:
        print("nearby_places suburbs error:", e)

    # 3️⃣ Nearby cities (GeoDB)
    nearby_cities = []
    try:
        nearby_url = f"https://geodb-free-service.wirefreethought.com/v1/geo/locations/{lat},{lon}/nearbyCities?radius=300&limit=6"
        nearby_res = requests.get(nearby_url, timeout=8).json()
        nearby_cities = [c["city"] for c in nearby_res.get("data", [])]
    except Exception as e:
        print("nearby_places geodb error:", e)

    # 4️⃣ Major cities of country
    major_cities = []
    try:
        world_url = "http://geodb-free-service.wirefreethought.com/v1/geo/cities?limit=50&sort=-population"
        world = requests.get(world_url, timeout=8).json()
        for c in world.get("data", []):
            if c["country"] == country:
                major_cities.append(c["city"])
    except Exception as e:
        print("nearby_places world error:", e)

    result = {
        "suburbs": suburbs[:6],
        "nearby_cities": nearby_cities[:6],
        "major_cities": major_cities[:6]
    }

    # ⏳ Cache for 1 hour
    set_cache(cache_key, result, 3600)

    return jsonify(result)

@app.route("/local_areas_analysis")
def local_areas_analysis():

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))

    # 🔥 CACHE FIRST (most important fix)
    key = f"colonies_{round(lat,2)}_{round(lon,2)}"
    cached = get_cache(key)
    if cached:
        print("⚡ Colonies CACHE HIT")
        return jsonify(cached)

    token = WAQI_TOKEN

    url = f"https://api.waqi.info/map/bounds/?token={token}&latlng={lat-0.5},{lon-0.5},{lat+0.5},{lon+0.5}"

    try:
        res = requests.get(url, timeout=6).json()
    except:
        return jsonify([])

    if res.get("status") != "ok" or not res.get("data"):
        return jsonify([])

    areas = []

    for s in res["data"][:8]:

        aqi_raw = s.get("aqi", 0)

        try:
            aqi = int(aqi_raw)
        except:
            continue

        status = (
            "Good" if aqi <= 80 else
            "Moderate" if aqi <= 120 else
            "Unhealthy"
        )

        areas.append({
            "name": s["station"]["name"],
            "aqi": aqi,
            "pm25": int(aqi * 0.4),
            "pm10": int(aqi * 0.6),
            "status": status
        })

    # 🔥 Cache 10 minutes
    set_cache(key, areas, 600)

    return jsonify(areas)


@app.route("/global_rank")
def global_rank():

    mode = request.args.get("mode","live")

    # 🌫 MOST POLLUTED CITIES (global realistic snapshot)
    MOST_POLLUTED = [
        {"city":"Delhi", "aqi":210},
        {"city":"Lahore", "aqi":198},
        {"city":"Dhaka", "aqi":185},
        {"city":"Karachi", "aqi":176},
        {"city":"Beijing", "aqi":165},
        {"city":"Shanghai", "aqi":158},
        {"city":"Tehran", "aqi":150},
        {"city":"Jakarta", "aqi":148},
        {"city":"Manila", "aqi":142},
        {"city":"Bangkok", "aqi":135}
    ]

    # 🌿 CLEANEST CITIES
    CLEANEST = [
        {"city":"Reykjavik", "aqi":20},
        {"city":"Oslo", "aqi":22},
        {"city":"Stockholm", "aqi":24},
        {"city":"Zurich", "aqi":26},
        {"city":"Helsinki", "aqi":27},
        {"city":"Sydney", "aqi":28},
        {"city":"Wellington", "aqi":29},
        {"city":"Vancouver", "aqi":30},
        {"city":"Melbourne", "aqi":32},
        {"city":"Toronto", "aqi":35}
    ]

    # 🌍 GLOBAL SNAPSHOT (balanced)
    GLOBAL_SNAPSHOT = [
        {"city":"Delhi", "aqi":210},
        {"city":"Beijing", "aqi":165},
        {"city":"London", "aqi":82},
        {"city":"New York", "aqi":75},
        {"city":"Tokyo", "aqi":68},
        {"city":"Paris", "aqi":70},
        {"city":"Dubai", "aqi":95},
        {"city":"Singapore", "aqi":60},
        {"city":"Sydney", "aqi":28},
        {"city":"Toronto", "aqi":35}
    ]

    if mode == "polluted":
        return jsonify(MOST_POLLUTED)

    if mode == "clean":
        return jsonify(CLEANEST)

    return jsonify(GLOBAL_SNAPSHOT)




# ---------------------------------------------------------
# SIMULATION ENGINE
# ---------------------------------------------------------


def _poly_center(poly):
    lat = sum(p[0] for p in poly) / len(poly)
    lon = sum(p[1] for p in poly) / len(poly)
    return lat, lon

# Grid spatial index: bucket centers by cell so proximity checks are O(1)
# per query instead of scanning every polygon. This is what lets the heat
# engines run WITHOUT sampling caps on large areas.
def _build_grid(polys, cell):
    grid = {}
    centers = []
    for poly in polys:
        if not poly:
            continue
        c = _poly_center(poly)
        centers.append(c)
        key = (int(c[0] // cell), int(c[1] // cell))
        grid.setdefault(key, []).append(c)
    return grid

def _near_in_grid(grid, pt, cell, radius):
    ki, kj = int(pt[0] // cell), int(pt[1] // cell)
    r2 = radius * radius
    for i in range(ki - 1, ki + 2):
        for j in range(kj - 1, kj + 2):
            for c in grid.get((i, j), ()):
                if (pt[0]-c[0])**2 + (pt[1]-c[1])**2 < r2:
                    return True
    return False

def generate_rooftop_heat(buildings, industry, green, aqi):

    heat_points = []

    if not buildings:
        return heat_points

    RADIUS = 0.01
    industry_grid = _build_grid(industry, RADIUS)
    green_grid    = _build_grid(green, RADIUS)
    aqi_score = min((aqi or 80) / 300, 0.3)

    # 🎯 Every building in the box gets a rooftop score — no sampling
    for b in buildings:
        if not b:
            continue
        b_center = _poly_center(b)

        score = 0.4 + aqi_score

        # near industry → more rooftop needed
        if _near_in_grid(industry_grid, b_center, RADIUS, RADIUS):
            score += 0.2

        # near green → less need
        if _near_in_grid(green_grid, b_center, RADIUS, RADIUS):
            score -= 0.2

        score = max(0.1, min(score, 1.0))

        heat_points.append({
            "lat": b_center[0],
            "lon": b_center[1],
            "intensity": score
        })

    return heat_points

def generate_ground_heat(layers, aqi):

    heat_points = []

    green = layers["green"]
    water = layers["water"]
    industry = layers["industry"]

    RADIUS = 0.02
    industry_grid = _build_grid(industry, RADIUS)
    water_grid    = _build_grid(water, RADIUS)
    aqi_score = min((aqi or 80) / 300, 0.3)

    # Candidate areas = ALL green polygons — no thinning, no point cap
    for poly in green:

        if not poly:
            continue
        c = _poly_center(poly)
        score = 0.5 + aqi_score

        # near industry = more plantation needed
        if _near_in_grid(industry_grid, c, RADIUS, RADIUS):
            score += 0.2

        # near water = less needed
        if _near_in_grid(water_grid, c, RADIUS, RADIUS):
            score -= 0.2

        score = max(0.1, min(score, 1.0))

        heat_points.append({
            "lat": c[0],
            "lon": c[1],
            "intensity": score
        })

    return heat_points

# 🌳 REAL GIS PLANTATION SUITABILITY
from shapely.geometry import Polygon, Point

def generate_real_plantation_zones(layers, north, south, east, west):

    rect_poly = Polygon([
        (west, south),
        (east, south),
        (east, north),
        (west, north)
    ])

    exclusion_polys = []

    for key in ["buildings", "water", "industry", "green"]:
        for coords in layers.get(key, []):
            try:
                poly = Polygon([(lon, lat) for lat, lon in coords])
                exclusion_polys.append(poly)
            except:
                continue

    plantation_points = []
    import random

    for _ in range(300):

        lat = random.uniform(south, north)
        lon = random.uniform(west, east)
        p = Point(lon, lat)

        if not rect_poly.contains(p):
            continue

        blocked = False
        for ex in exclusion_polys:
            if ex.contains(p):
                blocked = True
                break

        if not blocked:
            plantation_points.append([lat, lon])

    return plantation_points[:120]

# 🌿 GREEN SCORE CALCULATOR
def calculate_green_score(aqi, buildings, greens, water, rooftop_heat):

    score = 100

    # AQI penalty (0–25)
    score -= min((aqi or 0) / 8, 25)

    # Building density penalty (0–25)
    building_penalty = min(len(buildings) * 0.05, 25)
    score -= building_penalty

    # Green area bonus (0–20)
    green_bonus = min(len(greens) * 0.04, 20)
    score += green_bonus

    # Water bonus (0–15)
    if len(water) > 0:
        score += 10

    # Rooftop heat penalty (0–15)
    if rooftop_heat:
        avg_heat = sum(p["intensity"] for p in rooftop_heat) / len(rooftop_heat)
        score -= avg_heat * 10

    # Clamp score
    score = max(0, min(100, round(score)))
    return score

# ---------------------------------------------------------
# ANALYZE API
# ---------------------------------------------------------
@app.route("/analyze")
def analyze():

    # Bounding box params (rectangle mode)
    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    # Search mode params (city search)
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    # -----------------------------
    # MODE 1 — Rectangle Selected
    # -----------------------------
    if None not in (north, south, east, west):

        # ⚡ Cache analyze result for 10 min
        analyze_key = f"analyze_{round(north,3)}_{round(south,3)}_{round(east,3)}_{round(west,3)}"
        cached_analyze = get_cache(analyze_key)
        if cached_analyze:
            print("⚡ ANALYZE CACHE HIT")
            return jsonify(cached_analyze)

        # center of selected area
        lat = (north + south) / 2
        lon = (east + west) / 2

        # add buffer so data never empty
        buffer = 0.01  # ~1km

        # ✅ FIX 2: try/except so OSM timeout → empty layers not 500
        try:
            layers = get_osm_features_bbox(
                north + buffer,
                south - buffer,
                east + buffer,
                west - buffer
            )
            layers = clip_layers_to_bbox(layers, north, south, east, west)
        except Exception as e:
            print("OSM error in /analyze:", e)
            layers = {"roads":[],"water":[],"green":[],"industry":[],"buildings":[]}

        # 🔒 Safety fallback
        if layers is None:
            layers = {
                "roads": [],
                "water": [],
                "green": [],
                "industry": [],
                "buildings": []
            }

        aqi = get_real_aqi(lat, lon)
        ndvi_tile = None
        if EE_READY:
            try:
                ndvi_tile = get_ndvi_tile(north, south, east, west)
            except Exception as gee_err:
                print("GEE NDVI error:", gee_err)

        rooftop_heat = generate_rooftop_heat(
            layers["buildings"],
            layers["industry"],
            layers["green"],
            aqi
        )

        ground_heat = generate_ground_heat(layers, aqi)
        # 🌿 CALCULATE GREEN SCORE
        green_score = calculate_green_score(
            aqi,
            layers["buildings"],
            layers["green"],
            layers["water"],
            rooftop_heat
        )
        result_data = {
            "aqi": aqi,
            "center": {"lat": lat, "lon": lon},
            "layers": layers,
            "rooftop_heat": rooftop_heat,
            "ground_heat": ground_heat,
            "ndvi_tile": ndvi_tile,
            "green_score": green_score
        }
        set_cache(analyze_key, result_data, 600)
        return jsonify(result_data)

    if lat is not None and lon is not None:

        # ✅ FIX 2: same here
        try:
            layers = get_osm_features_bbox(
                lat+0.02, lat-0.02, lon+0.02, lon-0.02
            )
        except Exception as e:
            print("OSM error in /analyze lat/lon:", e)
            layers = {"roads":[],"water":[],"green":[],"industry":[],"buildings":[]}

        aqi = get_real_aqi(lat, lon)

        ndvi_tile = None
        if EE_READY:
            try:
                ndvi_tile = get_ndvi_tile(
                    lat+0.02, lat-0.02, lon+0.02, lon-0.02
                )
            except Exception as gee_err:
                print("GEE NDVI error:", gee_err)

        # ⭐ ADD SAME PIPELINE AS RECTANGLE MODE
        rooftop_heat = generate_rooftop_heat(
            layers["buildings"],
            layers["industry"],
            layers["green"],
            aqi
        )

        ground_heat = generate_ground_heat(layers, aqi)

        green_score = calculate_green_score(
            aqi,
            layers["buildings"],
            layers["green"],
            layers["water"],
            rooftop_heat
        )

        return jsonify({
            "aqi": aqi,
            "center": {"lat": lat, "lon": lon},
            "layers": layers,
            "rooftop_heat": rooftop_heat,
            "ground_heat": ground_heat,
            "ndvi_tile": ndvi_tile,
            "green_score": green_score
        })

    # Neither a bbox nor lat/lon was provided — return 400 instead of
    # falling through with None (which Flask turns into a 500)
    return jsonify({"error": "provide bbox params (n,s,e,w) or lat/lon"}), 400

# ---------------------------------------------------------

# AREA CALCULATOR (km²)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🧠 RECOMMENDATION ENGINE
# ---------------------------------------------------------


def generate_recommendations(aqi, buildings, industry, green, water, rooftop_heat):

    recs = []

    # 🌫 Pollution mitigation
    if aqi and aqi > 120:
        recs.append("Plant dense tree buffers near high pollution zones")

    # 🔥 Heat island mitigation
    if rooftop_heat:
        avg_heat = sum(p["intensity"] for p in rooftop_heat) / len(rooftop_heat)
        if avg_heat > 0.6:
            recs.append("Install rooftop gardens and cool roof coatings")

    # 🏭 Industry buffer
    if len(industry) > 10:
        recs.append("Create green buffer zones around industrial areas")

    # 🏠 Dense buildings
    if len(buildings) > 200:
        recs.append("Increase urban tree canopy along streets")

    # 💧 Water protection
    if len(water) > 0:
        recs.append("Protect water body buffer zones from construction")

    # 🌳 Low greenery
    if len(green) < 20:
        recs.append("Develop new urban parks and green corridors")

    if not recs:
        recs.append("Area is environmentally balanced. Maintain current policies.")

    return recs

# ---------------------------------------------------------
# 🌳 IMPACT API
# ---------------------------------------------------------
@app.route("/impact")
def impact():

    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    if None in (north, south, east, west):
        return {"error":"bbox missing"}, 400

    lat = (north + south) / 2
    lon = (east + west) / 2

    aqi = get_real_aqi(lat, lon) or 80  # AQI now cached via get_real_aqi

    impact = calculate_green_impact(north, south, east, west, aqi)

    return jsonify(impact)

# ---------------------------------------------------------
# 🧠 RECOMMENDATION API
# ---------------------------------------------------------
@app.route("/recommendations")
def recommendations():

    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    if None in (north, south, east, west):
        return {"error":"bbox missing"},400

    # ⚡ Use same buffer as /analyze so OSM cache is always hit
    buffer = 0.01

    # ✅ FIX 2: same here
    try:
        layers = get_osm_features_bbox(
            north + buffer,
            south - buffer,
            east  + buffer,
            west  - buffer
        )
    except Exception as e:
        print("OSM error in /recommendations:", e)
        layers = {"roads":[],"water":[],"green":[],"industry":[],"buildings":[]}

    lat_c = (north+south)/2
    lon_c = (east+west)/2
    aqi = get_real_aqi(lat_c, lon_c)  # also cached

    rooftop_heat = generate_rooftop_heat(
        layers["buildings"],
        layers["industry"],
        layers["green"],
        aqi
    )

    recs = generate_recommendations(
        aqi,
        layers["buildings"],
        layers["industry"],
        layers["green"],
        layers["water"],
        rooftop_heat
    )

    return jsonify({"recommendations": recs})

def calculate_bbox_area_km2(north, south, east, west):

    lat_km = 111.0
    avg_lat = (north + south) / 2

    height_km = abs(north - south) * lat_km
    width_km  = abs(east - west) * lat_km * math.cos(math.radians(avg_lat))

    return height_km * width_km

# ---------------------------------------------------------
# 🌳 TREE IMPACT ENGINE
# ---------------------------------------------------------
def calculate_green_impact(north, south, east, west, aqi):

    area_km2 = calculate_bbox_area_km2(north, south, east, west)
    area_m2 = area_km2 * 1_000_000

    # 🎯 Target canopy = 15% area
    target_green_area = area_m2 * 0.15

    # 🌳 1 tree ≈ 12 m² canopy
    trees_required = int(target_green_area / 12)

    # 🌡 Cooling model (max 3.5°C)
    cooling = min(3.5, 0.15 * 15)

    # 💨 AQI improvement (max 25 points)
    aqi_improvement = int(min(aqi * 0.12, 25))

    # 🌿 CO₂ absorption (tons/year) — mature urban tree ≈ 22 kg/yr
    co2_tons = round((trees_required * 22) / 1000, 1)

    # 💰 Cost model (indicative): sapling + planting ≈ $2.5/tree,
    # watering/care ≈ $0.8/tree/yr for the first 3 establishment years
    planting_cost = int(trees_required * 2.5)
    annual_maintenance = int(trees_required * 0.8)

    # ⏳ When benefits arrive (urban-forestry rules of thumb)
    benefit_timeline = [
        {"period": "6–12 months", "benefit": "Saplings establish, dust capture begins (~10% of full impact)"},
        {"period": "2–3 years",   "benefit": "First measurable AQI improvement (~40% of full impact)"},
        {"period": "5 years",     "benefit": "Canopy forms, street-level cooling felt (~75% of full impact)"},
        {"period": "8–10 years",  "benefit": "Mature canopy — full cooling, AQI and CO₂ impact reached"},
    ]

    return {
        "area_km2": round(area_km2, 2),
        "trees_required": trees_required,
        "expected_cooling_c": round(cooling, 1),
        "aqi_improvement": aqi_improvement,
        "co2_absorption_tons_per_year": co2_tons,
        "canopy_gain_percent": 15,
        "planting_cost_usd": planting_cost,
        "annual_maintenance_usd": annual_maintenance,
        "benefit_timeline": benefit_timeline
    }





# 🛰️ URBAN GROWTH FAST ROUTE
@app.route("/urban_growth")
def urban_growth():

    bbox_str = request.args.get("bbox")
    if not bbox_str:
        return {"error":"bbox missing"},400

    bbox = list(map(float, bbox_str.split(",")))

    # find nearest cached area
    user_center = bbox_center(bbox)

    best = None
    best_dist = 999

    for area,data in URBAN_CACHE.items():
        d = distance(user_center, bbox_center(data["bbox"]))
        if d < best_dist:
            best = data
            best_dist = d

    # 🔴 Generate demo "new construction zones"
    import random
    new_zones = []
    west,south,east,north = bbox

    for _ in range(120):  # number of red dots
        lat = random.uniform(south,north)
        lon = random.uniform(west,east)
        new_zones.append([lat,lon])

    # copy so the shared URBAN_CACHE entry is not mutated per-request
    result = dict(best)
    result["new_zones"] = new_zones

    return result

# ---------------------------------------------------------
# 🏙 URBAN FORECAST API
# ---------------------------------------------------------
@app.route("/forecast")
def forecast():

    bbox_str = request.args.get("bbox")
    if not bbox_str:
        return {"error":"bbox missing"},400

    bbox = list(map(float, bbox_str.split(",")))
    user_center = bbox_center(bbox)

    # nearest cached region find
    best = None
    best_key = None
    best_dist = 999

    for area,data in URBAN_CACHE.items():
        d = distance(user_center, bbox_center(data["bbox"]))
        if d < best_dist:
            best = data
            best_key = area
            best_dist = d

    built_2019 = best["2019_built_percent"]
    built_2024 = best["2024_built_percent"]

    # 📈 growth per year
    growth_rate = (built_2024 - built_2019) / 5

    # 🔮 future predictions
    built_2027 = round(built_2024 + growth_rate * 3, 1)
    built_2030 = round(built_2024 + growth_rate * 6, 1)

    return {
        "2019": built_2019,
        "2024": built_2024,
        "2027": built_2027,
        "2030": built_2030,
        "growth_rate_per_year": round(growth_rate,2),
        # so the UI can say which city's trend this estimate is based on
        "reference_city": (best_key or "").title()
    }

# 🌡 THERMAL HEATMAP ROUTE
@app.route("/heatmap")
def heatmap():

    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    if None in (north, south, east, west):
        return {"error": "bbox missing"}, 400

    tile = None
    if EE_READY:
        try:
            tile = get_thermal_tile(north, south, east, west)
        except Exception as e:
            print("GEE heatmap error:", e)

    return {
        "tile": tile,
        "avg_temp": 33.8
    }

# 🌳 SATELLITE PLANTATION SUITABILITY ROUTE
@app.route("/plantation_map")
def plantation_map():

    north = request.args.get("n", type=float)
    south = request.args.get("s", type=float)
    east  = request.args.get("e", type=float)
    west  = request.args.get("w", type=float)

    if None in (north, south, east, west):
        return {"error":"bbox missing"}, 400

    tile = None
    if EE_READY:
        try:
            tile = get_plantation_tile(north, south, east, west)
        except Exception as e:
            print("GEE plantation error:", e)

    return {"tile": tile}

@app.route("/environment_analytics")
def environment_analytics():
    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))

    # 🌫 AQI + forecast
    aqi_trend = [50,60,70,65,55,58,62]
    try:
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={WAQI_TOKEN}"
        res = requests.get(url, timeout=6).json()
        forecast = res["data"]["forecast"]["daily"]["pm25"]
        aqi_trend = [day["avg"] for day in forecast[:7]]
    except:
        pass

    # 🌦 Weather
    weather = {"temp": None, "humidity": None, "wind": None}
    try:
        w_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric"
        w = requests.get(w_url, timeout=6).json()
        weather = {
            "temp": w["main"]["temp"],
            "humidity": w["main"]["humidity"],
            "wind": w["wind"]["speed"]
        }
    except:
        pass

    # 🗺 Land use from OSM mini analyze
    try:
        layers = get_osm_features_bbox(lat+0.02, lat-0.02, lon+0.02, lon-0.02)
    except:
        layers = {"buildings":[],"green":[],"water":[],"industry":[],"roads":[]}

    total = (
        len(layers["buildings"])
        + len(layers["green"])
        + len(layers["water"])
        + len(layers["industry"])
    )

    if total == 0:
        total = 1

    land_use = {
        "Buildings %": round(len(layers["buildings"]) / total * 100, 1),
        "Green %": round(len(layers["green"]) / total * 100, 1),
        "Water %": round(len(layers["water"]) / total * 100, 1),
        "Industry %": round(len(layers["industry"]) / total * 100, 1)
    }

    return jsonify({
        "aqi_trend": aqi_trend,
        "weather": weather,
        "land_use": land_use
    })

@app.route("/environment_full")
def environment_full():

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))

    # ⚡ Cache for 10 minutes
    env_key = f"envfull_{round(lat,2)}_{round(lon,2)}"
    cached = get_cache(env_key)
    if cached:
        print("⚡ ENV_FULL CACHE HIT")
        return jsonify(cached)

    # ----------------------------------
    # 🌫 AQI + Trend (WAQI)
    # ✅ FIX 3: try/except — ye SIGKILL de raha tha
    # ----------------------------------
    aqi_trend = []
    aqi_value = None
    try:
        waqi_url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={WAQI_TOKEN}"
        waqi_res = requests.get(waqi_url, timeout=6).json()
        if waqi_res["status"] == "ok":
            aqi_value = waqi_res["data"]["aqi"]
            # WAQI returns "-" for stations without data; a string here
            # crashes the pollution_breakdown math below with a 500
            try:
                aqi_value = int(aqi_value)
            except (TypeError, ValueError):
                aqi_value = None
            forecast = waqi_res["data"]["forecast"]["daily"]["pm25"]
            for day in forecast[:7]:
                aqi_trend.append(day["avg"])
    except:
        aqi_trend = [50,60,70,65,55,58,62]

    # ----------------------------------
    # 🌦 Weather (OpenWeather)
    # ✅ FIX 3: try/except
    # ----------------------------------
    weather = {"temp": None, "humidity": None, "wind": None}
    try:
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric"
        w = requests.get(weather_url, timeout=6).json()
        weather = {
            "temp": w["main"]["temp"],
            "humidity": w["main"]["humidity"],
            "wind": w["wind"]["speed"]
        }
    except:
        pass

    # ----------------------------------
    # 🗺 Land Use (OSM)
    # ✅ FIX 3: try/except — ye main OSM crash tha
    # ----------------------------------
    try:
        layers = get_osm_features_bbox(lat+0.02, lat-0.02, lon+0.02, lon-0.02)
    except Exception as e:
        print("OSM error in environment_full:", e)
        layers = {"buildings":[],"green":[],"water":[],"industry":[],"roads":[]}

    total = max(1,
        len(layers["buildings"])
        + len(layers["green"])
        + len(layers["water"])
        + len(layers["industry"])
    )

    land_use = {
        "Buildings %": round(len(layers["buildings"]) / total * 100, 1),
        "Green %": round(len(layers["green"]) / total * 100, 1),
        "Water %": round(len(layers["water"]) / total * 100, 1),
        "Industry %": round(len(layers["industry"]) / total * 100, 1)
    }

    # ----------------------------------
    # 🧪 Pollution Breakdown (from AQI)
    # ----------------------------------
    base = aqi_value or 50

    pollution_breakdown = {
        "PM25": round(base * 0.4),
        "PM10": round(base * 0.3),
        "NO2":  round(base * 0.1),
        "O3":   round(base * 0.1),
        "SO2":  round(base * 0.05),
        "CO":   round(base * 0.05)
    }

    env_result = {
        "aqi": aqi_value,
        "aqi_trend": aqi_trend,
        "weather": weather,
        "land_use": land_use,
        "pollution_breakdown": pollution_breakdown
    }
    set_cache(env_key, env_result, 600)
    return jsonify(env_result)

from concurrent.futures import ThreadPoolExecutor

@app.route("/environment_context")
def environment_context():

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))

    # 🔥 CACHE FIRST (important)
    key = f"context_{round(lat,2)}_{round(lon,2)}"
    cached = get_cache(key)
    if cached:
        print("⚡ Context CACHE HIT")
        return jsonify(cached)

    headers = {"User-Agent":"GreenLens"}

    # ✅ FIX 4: Nominatim try/except — ye JSONDecodeError crash tha
    try:
        rev_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        rev = requests.get(rev_url, headers=headers, timeout=6).json()
        country = rev["address"]["country"]
        country = country.replace("Republic of ", "").replace("Kingdom of ", "").strip()
        city_name = rev["address"].get("city") or rev["address"].get("town") or "Selected City"
        country_code = rev["address"]["country_code"].upper()
    except Exception as e:
        print("Nominatim error in environment_context:", e)
        try:
            main_city = city_stats("Selected Area", lat, lon)
        except:
            main_city = {"name": "Selected Area", "lat": lat, "lon": lon, "aqi": None, "green_score": 0}
        return jsonify({"main_city": main_city, "major_cities": []})

    # 🟢 MAIN CITY (single call)
    main_city = city_stats(city_name, lat, lon)

    # 🌆 GET COUNTRY CODE
    try:
        geo_url = f"http://geodb-free-service.wirefreethought.com/v1/geo/cities?countryIds={country_code}&limit=10&sort=-population"
        res = requests.get(geo_url, timeout=8).json()
        city_list = []
        for c in res.get("data", []):
            city_list.append({
                "name": c["city"],
                "lat": c["latitude"],
                "lon": c["longitude"]
            })
            if len(city_list) == 5:
                break
    except Exception as e:
        print("GeoDB error:", e)
        city_list = []

    # 🚀 PARALLEL EXECUTION FOR MAJOR CITIES
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            major_cities = list(
                executor.map(
                    lambda c: city_stats(c["name"], c["lat"], c["lon"]),
                    city_list
                )
            )
    except Exception as e:
        print("city_stats parallel error:", e)
        major_cities = []

    result = {
        "main_city": main_city,
        "major_cities": major_cities
    }

    # 🔥 Cache 15 minutes
    set_cache(key, result, 900)

    return jsonify(result)



@app.route("/plantation_ai_report", methods=["POST"])
def plantation_ai_report():

    data = request.get_json()
    w, s, e, n = data["bbox"]

    lat = (n + s) / 2
    lon = (e + w) / 2

    headers = {"User-Agent": "GreenLens"}

    # 🌍 Detect country
    try:
        rev = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers=headers,
            timeout=6
        ).json()

        country_code = rev["address"]["country_code"].upper()
    except:
        country_code = "IN"

    ground_species = []
    rooftop_species = []
    seen = set()
    offset = 0

    # -----------------------------------
    # 🌿 1️⃣ TRY REAL GBIF FIRST
    # -----------------------------------
    while (len(ground_species) < 10 or len(rooftop_species) < 10) and offset < 500:

        url = (
            "https://api.gbif.org/v1/species/search?"
            f"country={country_code}"
            "&rank=SPECIES"
            "&kingdom=Plantae"
            "&habitat=terrestrial"
            "&limit=100"
            f"&offset={offset}"
        )

        try:
            res = requests.get(url, timeout=8).json()
        except:
            break

        results = res.get("results", [])
        if not results:
            break

        for sp in results:

            name = sp.get("canonicalName")
            if not name:
                continue

            # skip hybrids / bad entries
            if " x " in name.lower():
                continue

            if name in seen:
                continue

            seen.add(name)
            genus = name.split()[0]

            # 🌳 Large canopy → Ground
            if genus in [
                "Quercus","Fagus","Pinus","Cedrus",
                "Platanus","Populus","Acer","Tilia",
                "Albizia","Terminalia","Swietenia",
                "Eucalyptus","Acacia","Shorea",
                "Ficus","Fraxinus","Betula"
            ]:
                if len(ground_species) < 10:
                    ground_species.append(name)

            # 🏢 Small/container → Rooftop
            elif genus in [
                "Prunus","Citrus","Malus","Morus",
                "Lagerstroemia","Olea","Punica",
                "Callistemon","Camellia",
                "Magnolia","Cornus"
            ]:
                if len(rooftop_species) < 10:
                    rooftop_species.append(name)

            if len(ground_species) >= 10 and len(rooftop_species) >= 10:
                break

        offset += 100

    # -----------------------------------
    # 🔥 2️⃣ CONTINENT FALLBACK
    # -----------------------------------
    if len(ground_species) < 10 or len(rooftop_species) < 10:

        continent = COUNTRY_CONTINENT.get(country_code)
        if not continent:
            continent = "ASIA"

        backup = CONTINENT_TREES.get(continent)

        if backup:

            for sp in backup["ground"]:
                if sp not in ground_species and len(ground_species) < 10:
                    ground_species.append(sp)

            for sp in backup["rooftop"]:
                if sp not in rooftop_species and len(rooftop_species) < 10:
                    rooftop_species.append(sp)

    # -----------------------------------
    # 🛡️ 3️⃣ FINAL SAFETY GUARANTEE
    # -----------------------------------
    if len(ground_species) == 0:
        ground_species = CONTINENT_TREES["ASIA"]["ground"][:10]

    if len(rooftop_species) == 0:
        rooftop_species = CONTINENT_TREES["ASIA"]["rooftop"][:10]

    return jsonify({
        "ground_species": ground_species[:10],
        "rooftop_species": rooftop_species[:10],
        "zone_plan": {
            "roadside": 500,
            "parks": 800,
            "vacant_land": 1200,
            "rooftop": 200,
            "total_trees": 2700
        }
    })

# 🌳 SMART TREE TRAITS ENGINE (GLOBAL - NO HARDCODED DB)

def estimate_tree_traits(name):

    genus = name.split()[0].lower()

    # 🌞 Heat tolerant / pollution hardy trees
    if genus in [
        "ficus","azadirachta","acacia","eucalyptus",
        "polyalthia","pongamia","quercus","platanus"
    ]:
        lifespan = "100 – 200 years"
        water = "Low – Medium"
        heat = "High"
        co2 = "28 – 35 kg / year"

    # 🌸 Ornamental / medium trees
    elif genus in [
        "delonix","jacaranda","lagerstroemia",
        "cassia","tabebuia"
    ]:
        lifespan = "60 – 100 years"
        water = "Medium"
        heat = "Medium"
        co2 = "20 – 25 kg / year"

    # 🍊 Small / rooftop / fruit trees
    elif genus in [
        "citrus","prunus","malus","morus","olea"
    ]:
        lifespan = "30 – 60 years"
        water = "Medium – High"
        heat = "Medium"
        co2 = "12 – 18 kg / year"

    # 🌍 Unknown species fallback (GLOBAL SAFE)
    else:
        lifespan = "50 – 120 years"
        water = "Medium"
        heat = "Moderate"
        co2 = "18 – 25 kg / year"

    return lifespan, water, heat, co2   

from urllib.parse import quote

@app.route("/species_info")
def species_info():

    name = request.args.get("name")

    summary = None
    image = ""
    wiki_link = ""

    try:
        # ==================================================
        # 1️⃣ WIKIPEDIA DIRECT PAGE (PRIMARY SOURCE)
        # ==================================================
        wiki_title = name.replace(" ", "_")
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_title}"

        wiki = requests.get(summary_url, headers=HEADERS, timeout=10).json()

        summary = wiki.get("extract")
        image = wiki.get("thumbnail", {}).get("source", "")
        wiki_link = wiki.get("content_urls", {}).get("desktop", {}).get("page", "")

        # ==================================================
        # 2️⃣ WIKIPEDIA SEARCH FALLBACK (🔥 VERY IMPORTANT)
        # many plants don't match exact page name
        # ==================================================
        if not summary:

            search = requests.get(
                f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote(name)}&format=json",
                headers=HEADERS,
                timeout=10
            ).json()

            if search.get("query", {}).get("search"):
                title = search["query"]["search"][0]["title"]

                wiki = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
                    headers=HEADERS,
                    timeout=10
                ).json()

                summary = wiki.get("extract")
                image = wiki.get("thumbnail", {}).get("source", "")
                wiki_link = wiki.get("content_urls", {}).get("desktop", {}).get("page", "")

        # ==================================================
        # 3️⃣ WIKIDATA FALLBACK
        # ==================================================
        if not summary:

            wd_search = requests.get(
                f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={quote(name)}&language=en&format=json",
                headers=HEADERS,
                timeout=10
            ).json()

            if wd_search.get("search"):
                entity_id = wd_search["search"][0]["id"]

                entity = requests.get(
                    f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json",
                    headers=HEADERS,
                    timeout=10
                ).json()

                entity_data = entity["entities"][entity_id]

                summary = entity_data.get("descriptions", {}).get("en", {}).get("value")

        # ==================================================
        # 4️⃣ GBIF LAST FALLBACK
        # ==================================================
        if not summary:
            try:
                match = requests.get(
                    f"https://api.gbif.org/v1/species/match?name={quote(name)}",
                    headers=HEADERS,
                    timeout=10
                ).json()

                if "usageKey" in match:
                    key = match["usageKey"]

                    profile = requests.get(
                        f"https://api.gbif.org/v1/species/{key}",
                        headers=HEADERS,
                        timeout=10
                    ).json()

                    summary = (
                        profile.get("remarks") or
                        profile.get("habitats") or
                        profile.get("description")
                    )
            except:
                pass

        # ==================================================
        # 5️⃣ ABSOLUTE FINAL SAFETY TEXT
        # ==================================================
        if not summary:
            summary = "Plant species recorded in global biodiversity databases."

        # ==================================================
        # 6️⃣ TREE TRAITS (always runs now)
        # ==================================================
        lifespan, water, heat, co2 = estimate_tree_traits(name)

        return jsonify({
            "title": name,
            "summary": summary,
            "image": image,
            "wiki": wiki_link,
            "lifespan": lifespan,
            "water": water,
            "heat": heat,
            "co2": co2
        })

    except Exception as e:
        print("Species API error:", e)
        return jsonify({"error":"not found"})

# ======================
# RUN
# ======================

if __name__ == "__main__":
    # Local run: python app.py  →  http://127.0.0.1:5000/dashboard
    # host 0.0.0.0 also allows phones/other devices on the same network
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
