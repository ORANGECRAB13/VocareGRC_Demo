# Georges River Bin Zone API

An ElevenLabs-compatible API for looking up general waste collection zones and schedules for the Georges River Council area.

## 📋 Overview

This API allows voice agents to answer the question: **"When is my bin collected?"**

It uses point-in-polygon geospatial lookups to determine which collection zone an address falls into, then returns the collection schedule and next collection date.

### Features

- ✅ **Coordinate-based lookup** — Fast point-in-polygon queries
- ✅ **Address-based lookup** — Geocode addresses to coordinates (requires Google Maps API)
- ✅ **Next collection date** — Automatically calculates the next scheduled collection
- ✅ **Voice-ready responses** — Returns natural language prompts for TTS
- ✅ **Zone information** — Lists all collection days for a zone
- ✅ **ElevenLabs integration** — Drop-in tool for voice agents

## 🚀 Quick Start

### 1. Install Dependencies

```bash
npm install
```

### 2. Set Up Zone Files

Create a `zones/` directory and add your GeoJSON files:

```
project/
├── bin-zone-api.js
├── package.json
├── zones/
│   ├── mon_zone1.geojson
│   ├── mon_zone2.geojson
│   ├── tues_zone1.geojson
│   ├── tues_zone2.geojson
│   ├── wed_zone1.geojson
│   ├── wed_zone2.geojson
│   ├── thurs_zone1.geojson
│   ├── thurs_zone2.geojson
│   ├── fri_zone1.geojson
│   └── fri_zone2.geojson
```

### 3. Test Configuration

```bash
npm test
```

This validates:
- ✓ GeoJSON file format
- ✓ Zone loading and parsing
- ✓ Point-in-polygon functionality
- ✓ Next collection date calculation

### 4. Start the API

```bash
npm start
```

Server runs on `http://localhost:3000`

### 5. Test Endpoints

```bash
# Health check
curl http://localhost:3000/health

# Lookup by coordinates
curl "http://localhost:3000/bin-zone?lat=-33.968&lng=151.048"

# List all zones
curl http://localhost:3000/zones
```

## 📡 API Endpoints

### GET `/bin-zone`

Look up bin zone by coordinates.

**Query Parameters:**
- `lat` (required, number) — Latitude
- `lng` (required, number) — Longitude

**Response:**
```json
{
  "zone_id": 1,
  "collection_schedule": {
    "general_waste": ["monday", "friday"],
    "next_collection_day": {
      "day": "monday",
      "date": "2026-04-20",
      "days_until": 2
    },
    "bin_type": "General Waste"
  },
  "coordinate": {
    "lat": -33.968,
    "lng": 151.048
  },
  "voice_prompt": "Your address is in Zone 1. General waste is collected on Monday and Friday."
}
```

**Example:**
```bash
curl "http://localhost:3000/bin-zone?lat=-33.968&lng=151.048"
```

---

### POST `/bin-zone/address`

Look up bin zone by address (requires Google Maps API key).

**Request Body:**
```json
{
  "address": "123 Main Street, Hurstville NSW 2220"
}
```

**Response:**
```json
{
  "zone_id": 1,
  "address": "123 Main Street, Hurstville NSW 2220, Australia",
  "collection_schedule": {
    "general_waste": ["monday", "friday"],
    "next_collection_day": {
      "day": "monday",
      "date": "2026-04-20",
      "days_until": 2
    },
    "bin_type": "General Waste"
  },
  "coordinate": {
    "lat": -33.968,
    "lng": 151.048
  },
  "voice_prompt": "Your address is in Zone 1. General waste is collected on Monday and Friday."
}
```

**Example:**
```bash
curl -X POST http://localhost:3000/bin-zone/address \
  -H "Content-Type: application/json" \
  -d '{"address":"123 Main Street, Hurstville NSW 2220"}'
```

---

### GET `/zones`

List all loaded zones and their collection days.

**Response:**
```json
{
  "total_zones": 2,
  "zones": [
    {
      "zone_id": 1,
      "collection_days": ["monday", "friday"]
    },
    {
      "zone_id": 2,
      "collection_days": ["wednesday", "saturday"]
    }
  ]
}
```

---

### GET `/health`

Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "zones_loaded": 2,
  "timestamp": "2026-04-18T10:30:00.000Z"
}
```

## 🤖 ElevenLabs Integration

### Register the Tool

In your ElevenLabs agent configuration, add this tool:

```json
{
  "name": "get_bin_collection_zone",
  "description": "Get the bin collection zone and schedule for a given address or coordinates",
  "parameters": {
    "type": "object",
    "properties": {
      "lat": {
        "type": "number",
        "description": "Latitude coordinate"
      },
      "lng": {
        "type": "number",
        "description": "Longitude coordinate"
      }
    },
    "required": ["lat", "lng"]
  },
  "endpoint": "https://your-api.com/bin-zone"
}
```

### Example Conversation

**User:** "When is my bin collected?"

**Agent:**
1. Detects user's location (GPS/IP)
2. Calls `GET /bin-zone?lat=-33.968&lng=151.048`
3. Receives `voice_prompt`: "Your address is in Zone 1. General waste is collected on Monday and Friday."
4. Speaks response to user

## 🔧 Configuration

### Environment Variables

Create a `.env` file:

```env
PORT=3000
NODE_ENV=production
GOOGLE_MAPS_API_KEY=your-google-maps-api-key  # Optional, for address lookup
```

### Enable Google Maps Address Lookup

1. Create a Google Maps API key: https://console.cloud.google.com
2. Enable the **Geocoding API**
3. Set the environment variable:
   ```bash
   export GOOGLE_MAPS_API_KEY=your-key
   ```

## 📊 GeoJSON File Format

Files are expected to follow this naming convention:

```
{day}_{zone}.geojson
```

Examples:
- `mon_zone1.geojson` — Monday collections, Zone 1
- `fri_zone2.geojson` — Friday collections, Zone 2

Each file should contain a Feature or FeatureCollection with Polygon geometries:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[lng, lat], [lng, lat], ...]]
      },
      "properties": {}
    }
  ]
}
```

**Note:** GeoJSON uses **[longitude, latitude]** order!

## 🧪 Testing

### Run Test Suite

```bash
npm test
```

Tests:
- ✓ Zone directory exists
- ✓ GeoJSON files parse correctly
- ✓ Zone structure is valid
- ✓ Point-in-polygon lookups work
- ✓ Next collection day calculation is correct

### Manual Testing

```bash
# Test with coordinates in your zone
curl "http://localhost:3000/bin-zone?lat=-33.968&lng=151.048"

# Test outside zones (should return 404)
curl "http://localhost:3000/bin-zone?lat=-33.5&lng=150.5"

# List all loaded zones
curl http://localhost:3000/zones
```

## 🚢 Deployment

### Vercel (Recommended)

```bash
npm i -g vercel
vercel
```

Add zones to a `public/zones/` folder or configure a file upload endpoint.

### Heroku

```bash
heroku create
git push heroku main
heroku config:set GOOGLE_MAPS_API_KEY=your-key
```

### Docker

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
EXPOSE 3000
CMD ["npm", "start"]
```

```bash
docker build -t bin-zone-api .
docker run -p 3000:3000 -v $(pwd)/zones:/app/zones bin-zone-api
```

## 🐛 Troubleshooting

### "Address not found in any collection zone"

- Verify the coordinates are within Georges River Council boundaries
- Check that GeoJSON files cover the target area
- Inspect zones with `GET /zones`

### "Zones directory not found"

```bash
mkdir zones
# Add your GeoJSON files
npm test
```

### "Google Maps API key not configured"

- For coordinate-based lookup: **No key needed** ✓
- For address-based lookup: Set `GOOGLE_MAPS_API_KEY` environment variable

### "GeoJSON parsing error"

- Validate your GeoJSON at [geojson.io](https://geojson.io)
- Check filename matches pattern: `{day}_{zone}.geojson`
- Ensure coordinates are [longitude, latitude]

## 📚 Documentation

- [ElevenLabs Integration Guide](./ELEVENLABS_INTEGRATION.md)
- [GeoJSON Specification](https://tools.ietf.org/html/rfc7946)
- [Google Maps Geocoding API](https://developers.google.com/maps/documentation/geocoding/overview)

## 📄 License

MIT

## 🤝 Support

For issues or questions:
1. Check the Troubleshooting section above
2. Review API logs: `npm start` (verbose output)
3. Validate GeoJSON: `npm test`
4. Check ElevenLabs documentation for tool integration
