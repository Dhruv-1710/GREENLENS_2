# GreenLens

**Environmental Intelligence Platform** — satellite-based vegetation and heat analysis, real-time air quality tracking, and AI-assisted plantation planning, wrapped in a community platform.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/Flask-Backend-black)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Security](#security)
- [License](#license)
- [Author](#author)

---

## Overview

GreenLens lets a user select any area on a map and get back a consolidated environmental picture of that region — vegetation health, surface heat, air quality, and land use — along with a suggested tree-plantation plan and species list. It also includes a small community layer (feed, messaging, events) for users to share environmental initiatives.

The backend is a single Flask application exposing a set of analysis endpoints; the frontend is server-rendered HTML with Leaflet.js for mapping and Chart.js for data visualization.

---

## Features

### Satellite & Environmental Analysis
- NDVI vegetation health via Google Earth Engine (Sentinel-2), with an OpenStreetMap-based fallback when GEE credentials aren't configured
- Urban heat island detection using Landsat 8/9 thermal bands
- Satellite-derived plantation suitability zones
- Urban expansion tracking using cached built-up-area data

### Air Quality & Weather
- Live AQI lookup via WAQI, with health advisories
- 7-day AQI trend and PM2.5 charts
- Temperature, humidity, and wind data via OpenWeatherMap
- Pollutant breakdown — PM2.5, PM10, NO2, O3, SO2, CO

### AI Plantation Engine
- Region-aware species recommendations sourced from the GBIF biodiversity database
- Continent-level fallback tree list when no local GBIF match is found
- Zone-based planting plan — roadside, parks, rooftop, vacant land
- Species reference data from Wikipedia/Wikidata
- Estimated impact — CO₂ absorption, cooling effect, and AQI improvement

### Urban Intelligence
- OpenStreetMap-based land use breakdown — buildings, green cover, water bodies, industrial zones
- Green Score for any selected area
- Rooftop heat index for solar/cooling suitability
- Global city AQI rankings

### Community Platform
- Social feed — posts, likes, comments, reposts
- Direct messaging and inbox
- Notifications
- Admin dashboard for event, user, and content management
- Password strength validation and brute-force login protection

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask, Flask-SQLAlchemy |
| Database | SQLite (via SQLAlchemy ORM) |
| Satellite Data | Google Earth Engine (Sentinel-2, Landsat 8/9) |
| Maps | Leaflet.js, OpenStreetMap, Overpass API |
| Air Quality | WAQI API |
| Weather | OpenWeatherMap API |
| Biodiversity | GBIF Species API |
| Geocoding | Nominatim (OSM) |
| Auth | Session-based, Werkzeug password hashing (pbkdf2:sha256) |
| Deployment | Gunicorn |

---

## Getting Started

### Prerequisites
- Python 3.10+
- pip

### Installation

```bash
git clone https://github.com/Dhruv-1710/greenlens.git
cd greenlens
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

The application runs without any API keys, falling back to OpenStreetMap data and reasonable defaults. To enable live data sources, add the following to `.env`:

| Variable | Enables | Source |
|---|---|---|
| `WEATHER_KEY` | Live temperature, humidity, wind | [openweathermap.org/api](https://openweathermap.org/api) |
| `WAQI_TOKEN` | Live AQI and station data | [aqicn.org/data-platform/token](https://aqicn.org/data-platform/token/) |
| `GEE_PROJECT`, `GEE_EMAIL`, `GEE_KEY_FILE` | Satellite NDVI/thermal tiles | [earthengine.google.com](https://earthengine.google.com/) |

### Run

```bash
python app.py
```

The app will be available at `http://localhost:5000`.

---

## Project Structure

```
greenlens/
├── app.py                 # Flask application — routes and business logic
├── urban_cache.json        # Pre-cached urban expansion data
├── requirements.txt
├── .env.example             # Environment variable template
├── templates/
│   ├── landing.html         # Landing page
│   ├── dashboard.html       # Satellite analysis dashboard
│   ├── social.html          # Community feed
│   ├── profile.html         # User profile
│   ├── messages.html        # Direct messaging
│   ├── inbox.html           # Message inbox
│   ├── auth.html            # Login / signup
│   └── admin.html           # Admin panel
└── static/                  # Static assets
```

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /analyze` | Full environmental analysis for a bounding box or coordinate |
| `GET /ndvi` | NDVI vegetation tile from Sentinel-2 |
| `GET /heatmap` | Thermal heatmap tile from Landsat |
| `GET /plantation_map` | Satellite-derived plantation suitability layer |
| `POST /plantation_ai_report` | Species recommendations for a bounding box |
| `GET /recommendations` | GIS-based environmental recommendations |
| `GET /impact` | Estimated impact calculator (trees, CO₂, cooling) |
| `GET /urban_growth` | Urban expansion data for a bounding box |
| `GET /environment_full` | Combined AQI, weather, land use, and pollution data |
| `GET /city_aqi` | Live AQI and weather for a coordinate |
| `GET /global_rank` | Global city AQI rankings |
| `GET /species_info` | Tree species reference data |
| `GET /search` | City geocoding |

---

## Security

- API keys are loaded from environment variables and never hardcoded
- Sessions use `httponly` cookies
- Passwords are hashed with `pbkdf2:sha256` via Werkzeug
- Login attempts are rate-limited with a temporary lockout after repeated failures
- Admin-only routes are verified server-side by role check

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Author

**Dhruv Yadav**
[GitHub](https://github.com/Dhruv-1710) · [LinkedIn](https://www.linkedin.com/in/dhruv-yadav-95b1302ba/)
