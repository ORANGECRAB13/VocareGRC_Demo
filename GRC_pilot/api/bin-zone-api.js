require('dotenv').config({ path: '../.env' });
const express = require('express');
const fs = require('fs');
const path = require('path');
const pointInPolygon = require('point-in-polygon');

const app = express();
app.use(express.json());

// Zone data structure
const zones = {};

// Days of week mapping
const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'];
const DAY_ABBREVIATIONS = {
  'mon': 'monday',
  'tues': 'tuesday',
  'wed': 'wednesday',
  'thurs': 'thursday',
  'fri': 'friday'
};

/**
 * Load all GeoJSON zone files into memory
 */
function loadZones() {
  const zonesDir = path.join(__dirname, 'zones');
  
  if (!fs.existsSync(zonesDir)) {
    console.warn(`⚠️  Zones directory not found at ${zonesDir}`);
    return;
  }

  const files = fs.readdirSync(zonesDir).filter(f => f.endsWith('.geojson'));
  
  files.forEach(file => {
    const match = file.match(/^(\w+)_zone(\d+)\.geojson$/i);
    if (!match) {
      console.warn(`⚠️  Skipping file with unexpected name: ${file}`);
      return;
    }

    const [, dayAbbr, zoneNum] = match;
    const day = DAY_ABBREVIATIONS[dayAbbr.toLowerCase()];
    const zoneKey = `${day}_zone_${zoneNum}`;

    if (!day) {
      console.warn(`⚠️  Unknown day abbreviation in filename: ${dayAbbr}`);
      return;
    }

    try {
      const filePath = path.join(zonesDir, file);
      const geojson = JSON.parse(fs.readFileSync(filePath, 'utf8'));

      if (!zones[zoneKey]) {
        zones[zoneKey] = {
          zone_id: zoneKey,
          collection_days: {},
          polygons: []
        };
      }

      // Store the day for this zone
      zones[zoneKey].collection_days[day] = true;

      // Store polygons for point-in-polygon lookup
      if (geojson.features && geojson.features.length > 0) {
        geojson.features.forEach(feature => {
          if (feature.geometry.type === 'Polygon') {
            feature.geometry.coordinates[0].forEach(coord => {
              zones[zoneKey].polygons.push({
                coordinates: feature.geometry.coordinates[0],
                day: day
              });
            });
          }
        });
      }

      console.log(`✓ Loaded ${file} → ${zoneKey} (${day})`);
    } catch (error) {
      console.error(`✗ Error loading ${file}:`, error.message);
    }
  });

  console.log(`\n📦 Loaded ${Object.keys(zones).length} zones with schedules:\n`);
  Object.entries(zones).forEach(([key, data]) => {
    const days = Object.keys(data.collection_days).join(', ');
    console.log(`  ${key}: ${days}`);
  });
}

/**
 * Find which zone a point falls into
 * @param {number} lat - Latitude
 * @param {number} lng - Longitude
 * @returns {object|null} - Zone data or null if not found
 */
function findZoneForPoint(lat, lng) {
  const point = [lng, lat]; // GeoJSON uses [lon, lat]

  for (const [zoneKey, zoneData] of Object.entries(zones)) {
    // Check if point is in any polygon for this zone
    for (const poly of zoneData.polygons) {
      if (pointInPolygon(point, poly.coordinates)) {
        return zoneData;
      }
    }
  }

  return null;
}

/**
 * GET /bin-zone
 * Query params: lat, lng
 * Returns: zone info + collection schedule
 */
app.get('/bin-zone', (req, res) => {
  const { lat, lng } = req.query;

  if (!lat || !lng) {
    return res.status(400).json({
      error: 'Missing required parameters',
      required: ['lat', 'lng'],
      example: '?lat=-33.968&lng=151.048'
    });
  }

  const latitude = parseFloat(lat);
  const longitude = parseFloat(lng);

  if (isNaN(latitude) || isNaN(longitude)) {
    return res.status(400).json({
      error: 'Invalid latitude or longitude',
      received: { lat, lng }
    });
  }

  const zone = findZoneForPoint(latitude, longitude);

  if (!zone) {
    return res.status(404).json({
      error: 'Address not found in any collection zone',
      coordinate: { lat: latitude, lng: longitude },
      suggestion: 'Verify the address is within Georges River Council boundaries'
    });
  }

  // Format response
  const collectionDays = Object.keys(zone.collection_days).sort((a, b) => {
    return DAYS.indexOf(a) - DAYS.indexOf(b);
  });

  const response = {
    zone_id: zone.zone_id,
    collection_schedule: {
      general_waste: collectionDays,
      next_collection_day: getNextCollectionDay(collectionDays),
      bin_type: 'General Waste'
    },
    coordinate: { lat: latitude, lng: longitude },
    voice_prompt: formatVoicePrompt(zone.zone_id, collectionDays)
  };

  res.json(response);
});

/**
 * Format a natural language prompt for the voice agent
 */
function formatVoicePrompt(zoneId, days) {
  const daysReadable = days
    .map(d => d.charAt(0).toUpperCase() + d.slice(1))
    .join(', ');

  return `Your bin collection is every ${daysReadable}.`;
}

/**
 * Get the next collection day from a list of collection days
 */
function getNextCollectionDay(collectionDays) {
  const today = new Date();
  const todayDay = today.getDay(); // 0 = Sunday, 1 = Monday, etc.
  
  const dayMap = {
    'monday': 1,
    'tuesday': 2,
    'wednesday': 3,
    'thursday': 4,
    'friday': 5,
    'saturday': 6,
    'sunday': 0
  };

  // Convert to numeric days
  const collectionNumDays = collectionDays.map(d => dayMap[d]);
  
  // Find next occurrence
  for (let i = 0; i < 7; i++) {
    const nextDay = (todayDay + i) % 7;
    if (collectionNumDays.includes(nextDay)) {
      const nextDate = new Date(today);
      nextDate.setDate(today.getDate() + i);
      return {
        day: collectionDays[collectionNumDays.indexOf(nextDay)],
        date: nextDate.toISOString().split('T')[0],
        days_until: i
      };
    }
  }

  return null;
}

/**
 * POST /bin-zone/address
 * Body: { address: "string" }
 * Geocodes address and returns zone (requires Google Maps API key)
 */
app.post('/bin-zone/address', async (req, res) => {
  const { address } = req.body;

  if (!address) {
    return res.status(400).json({
      error: 'Missing address',
      required: ['address']
    });
  }

  // Note: Requires Google Maps API key set in environment
  const apiKey = process.env.GOOGLE_MAPS_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: 'Google Maps API key not configured',
      note: 'Set GOOGLE_MAPS_API_KEY environment variable'
    });
  }

  try {
    const fetch = (await import('node-fetch')).default;
    const geocodeUrl = `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(address)}&key=${apiKey}`;
    const geocodeRes = await fetch(geocodeUrl);
    const geocodeData = await geocodeRes.json();

    if (geocodeData.results.length === 0) {
      return res.status(404).json({
        error: 'Address not found',
        address: address
      });
    }

    const { lat, lng } = geocodeData.results[0].geometry.location;
    const zone = findZoneForPoint(lat, lng);

    if (!zone) {
      return res.status(404).json({
        error: 'Address is outside collection zones',
        address: geocodeData.results[0].formatted_address,
        coordinate: { lat, lng }
      });
    }

    const collectionDays = Object.keys(zone.collection_days).sort((a, b) => {
      return DAYS.indexOf(a) - DAYS.indexOf(b);
    });

    res.json({
      zone_id: zone.zone_id,
      address: geocodeData.results[0].formatted_address,
      collection_schedule: {
        general_waste: collectionDays,
        next_collection_day: getNextCollectionDay(collectionDays),
        bin_type: 'General Waste'
      },
      coordinate: { lat, lng },
      voice_prompt: formatVoicePrompt(zone.zone_id, collectionDays)
    });
  } catch (error) {
    res.status(500).json({
      error: 'Geocoding failed',
      details: error.message
    });
  }
});

/**
 * GET /health
 * Health check endpoint
 */
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    zones_loaded: Object.keys(zones).length,
    timestamp: new Date().toISOString()
  });
});

/**
 * GET /zones
 * List all loaded zones
 */
app.get('/zones', (req, res) => {
  const zoneList = Object.entries(zones).map(([key, data]) => ({
    zone_id: data.zone_id,
    collection_days: Object.keys(data.collection_days).sort((a, b) => {
      return DAYS.indexOf(a) - DAYS.indexOf(b);
    })
  }));

  res.json({
    total_zones: zoneList.length,
    zones: zoneList
  });
});

// Load zones on startup
loadZones();

// Start server
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`\n🚀 Bin Zone API listening on http://localhost:${PORT}`);
  console.log(`📍 Try: GET /bin-zone?lat=-33.968&lng=151.048`);
  console.log(`📍 Or:  POST /bin-zone/address with { "address": "..." }`);
  console.log(`🏥 Health: GET /health`);
  console.log(`📋 Zones: GET /zones\n`);
});

module.exports = app;
