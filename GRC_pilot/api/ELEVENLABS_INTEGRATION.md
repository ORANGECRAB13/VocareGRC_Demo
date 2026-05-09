# ElevenLabs Integration Guide

This guide shows how to integrate the Bin Zone API with your ElevenLabs voice agent.

## Quick Start

### 1. Deploy the API

```bash
npm install
npm start
```

The API will be running on `http://localhost:3000`

For production, deploy to:
- **Vercel** (serverless)
- **Heroku**
- **Railway**
- **DigitalOcean App Platform**

### 2. Configure ElevenLabs Agent

In your ElevenLabs agent, add a custom tool that calls the bin zone API:

```json
{
  "name": "get_bin_collection_zone",
  "description": "Get the bin collection zone and schedule for a given address or coordinates",
  "parameters": {
    "type": "object",
    "properties": {
      "lat": {
        "type": "number",
        "description": "Latitude coordinate (e.g., -33.968)"
      },
      "lng": {
        "type": "number",
        "description": "Longitude coordinate (e.g., 151.048)"
      },
      "address": {
        "type": "string",
        "description": "Full address (alternative to lat/lng)"
      }
    },
    "required": ["lat", "lng"]
  }
}
```

### 3. API Endpoint Reference

#### Option A: Lookup by Coordinates

**GET** `/bin-zone?lat=-33.968&lng=151.048`

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

#### Option B: Lookup by Address

**POST** `/bin-zone/address`

**Request Body:**
```json
{
  "address": "123 Main Street, Hurstville, NSW 2220"
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

### 4. ElevenLabs Agent Configuration

Here's how to set up the tool in your agent:

```python
# Example using ElevenLabs Python SDK
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="your-api-key")

# Define the bin zone tool
bin_zone_tool = {
    "name": "get_bin_collection_zone",
    "description": "Get the bin collection zone and schedule for an address",
    "parameters": {
        "type": "object",
        "properties": {
            "lat": {
                "type": "number",
                "description": "Latitude"
            },
            "lng": {
                "type": "number",
                "description": "Longitude"
            }
        },
        "required": ["lat", "lng"]
    },
    "tool_call_handler": lambda lat, lng: requests.get(
        f"http://your-api-domain.com/bin-zone?lat={lat}&lng={lng}"
    ).json()
}

# Create agent with tool
response = client.convai.create_agent(
    conversation_id="...",
    tools=[bin_zone_tool]
)
```

### 5. Conversation Flow

Here's how the conversation might flow:

**User:** "When is my bin collected?"

**Agent (thinking):**
1. Ask user for address or enable location access
2. Receive coordinates (lat/lng)
3. Call `/bin-zone?lat={lat}&lng={lng}`
4. Receive zone and schedule
5. Read the `voice_prompt` back to user

**Agent (speaking):** "Your address is in Zone 1. General waste is collected on Monday and Friday. Your next collection is Monday, in 2 days."

### 6. Environment Variables

Create a `.env` file:

```
PORT=3000
GOOGLE_MAPS_API_KEY=your-google-maps-key (optional, only needed for address lookup)
NODE_ENV=production
```

### 7. Testing Locally

```bash
# Test health check
curl http://localhost:3000/health

# Test coordinate lookup
curl "http://localhost:3000/bin-zone?lat=-33.968&lng=151.048"

# Test address lookup (requires Google Maps API key)
curl -X POST http://localhost:3000/bin-zone/address \
  -H "Content-Type: application/json" \
  -d '{"address":"123 Main Street, Hurstville NSW 2220"}'

# List all zones
curl http://localhost:3000/zones
```

### 8. Deployment Checklist

- [ ] Copy all GeoJSON files to `./zones/` directory
- [ ] Set `GOOGLE_MAPS_API_KEY` environment variable (optional)
- [ ] Run `npm install`
- [ ] Test with `npm test`
- [ ] Deploy to production
- [ ] Update ElevenLabs tool endpoint to production URL
- [ ] Test voice agent end-to-end

### 9. Voice Prompt Customization

The API returns a `voice_prompt` field that's ready to speak. You can customize it in the code:

```javascript
function formatVoicePrompt(zoneId, days) {
  const daysReadable = days
    .map(d => d.charAt(0).toUpperCase() + d.slice(1))
    .join(' and ');

  // Customize here
  return `Your address is in Zone ${zoneId}. General waste is collected on ${daysReadable}.`;
}
```

### 10. Troubleshooting

**"Address not found in any collection zone"**
- Verify address is within Georges River Council boundaries
- Check lat/lng coordinates are correct
- Confirm GeoJSON files are in `./zones/` directory

**"Google Maps API key not configured"**
- Set `GOOGLE_MAPS_API_KEY` environment variable
- Or use coordinate lookup instead

**"Zones directory not found"**
- Create `./zones/` directory
- Add all GeoJSON files: `mon_zone1.geojson`, `fri_zone2.geojson`, etc.

---

**Ready to integrate?** Start with the coordinate-based endpoint—it's the fastest path to a working voice agent!
