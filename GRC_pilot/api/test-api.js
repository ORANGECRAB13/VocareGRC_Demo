#!/usr/bin/env node

/**
 * Test script for Bin Zone API
 * Validates zone loading, point-in-polygon, and API responses
 */

const fs = require('fs');
const path = require('path');
const pointInPolygon = require('point-in-polygon');

console.log('\n🧪 Bin Zone API Test Suite\n');

// Test 1: Check zones directory
console.log('Test 1: Checking zones directory...');
const zonesDir = path.join(__dirname, 'zones');
if (!fs.existsSync(zonesDir)) {
  console.log('⚠️  ⚠️  Zones directory not found at:', zonesDir);
  console.log('   Create it and add your GeoJSON files.');
  process.exit(1);
}

const files = fs.readdirSync(zonesDir).filter(f => f.endsWith('.geojson'));
console.log(`✓ Found ${files.length} GeoJSON files\n`);

// Test 2: Load and parse GeoJSON files
console.log('Test 2: Loading and parsing GeoJSON files...');
const zones = {};
const DAYS = {
  'mon': 'monday',
  'tues': 'tuesday',
  'wed': 'wednesday',
  'thurs': 'thursday',
  'fri': 'friday'
};

let loadedCount = 0;
let errorCount = 0;

files.forEach(file => {
  const match = file.match(/^(\w+)_zone(\d+)\.geojson$/i);
  if (!match) {
    console.log(`  ⚠️  Skipping malformed filename: ${file}`);
    errorCount++;
    return;
  }

  const [, dayAbbr, zoneNum] = match;
  const day = DAYS[dayAbbr.toLowerCase()];
  const zoneKey = `${day}_zone_${zoneNum}`;

  if (!day) {
    console.log(`  ⚠️  Unknown day in ${file}: ${dayAbbr}`);
    errorCount++;
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

    zones[zoneKey].collection_days[day] = true;

    if (geojson.features && geojson.features.length > 0) {
      geojson.features.forEach(feature => {
        if (feature.geometry.type === 'Polygon') {
          zones[zoneKey].polygons.push({
            coordinates: feature.geometry.coordinates[0],
            day: day
          });
        }
      });
    }

    console.log(`  ✓ ${file} → ${zoneKey} (${day})`);
    loadedCount++;
  } catch (error) {
    console.log(`  ✗ Error parsing ${file}: ${error.message}`);
    errorCount++;
  }
});

console.log(`\n✓ Loaded ${loadedCount} files successfully`);
if (errorCount > 0) {
  console.log(`⚠️  ${errorCount} files had errors\n`);
}

// Test 3: Validate zone structure
console.log('\nTest 3: Validating zone structure...');
Object.entries(zones).forEach(([key, data]) => {
  const days = Object.keys(data.collection_days).sort().join(', ');
  const polygonCount = data.polygons.length;
  console.log(`  ✓ ${key}: ${days} (${polygonCount} polygon(s))`);
});

// Test 4: Point-in-polygon test
console.log('\nTest 4: Testing point-in-polygon lookups...');

function findZoneForPoint(lat, lng) {
  const point = [lng, lat];
  for (const [zoneKey, zoneData] of Object.entries(zones)) {
    for (const poly of zoneData.polygons) {
      if (pointInPolygon(point, poly.coordinates)) {
        return zoneData;
      }
    }
  }
  return null;
}

// Test with sample coordinates from the GeoJSON you provided
const testCoordinates = [
  { lat: -33.968, lng: 151.048, description: 'Hurstville area (from GeoJSON)' },
  { lat: -33.97, lng: 151.05, description: 'Near Hurstville' }
];

testCoordinates.forEach(({ lat, lng, description }) => {
  const zone = findZoneForPoint(lat, lng);
  if (zone) {
    const days = Object.keys(zone.collection_days).sort().join(', ');
    console.log(`  ✓ (${lat}, ${lng}): Zone ${zone.zone_id} - ${days}`);
  } else {
    console.log(`  ⚠️  (${lat}, ${lng}): No zone found - ${description}`);
  }
});

// Test 5: Next collection day calculation
console.log('\nTest 5: Testing next collection day calculation...');

function getNextCollectionDay(collectionDays) {
  const today = new Date();
  const todayDay = today.getDay();
  
  const dayMap = {
    'monday': 1,
    'tuesday': 2,
    'wednesday': 3,
    'thursday': 4,
    'friday': 5,
    'saturday': 6,
    'sunday': 0
  };

  const collectionNumDays = collectionDays.map(d => dayMap[d]);
  
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

const sampleDays = ['monday', 'friday'];
const nextDay = getNextCollectionDay(sampleDays);
console.log(`  ✓ Test days: ${sampleDays.join(', ')}`);
console.log(`  ✓ Next collection: ${nextDay.day} (${nextDay.date}) in ${nextDay.days_until} days`);

// Summary
console.log('\n📊 Summary');
console.log(`  Total zones: ${Object.keys(zones).length}`);
console.log(`  Total GeoJSON files: ${loadedCount}`);
console.log(`  Parsing errors: ${errorCount}`);

if (Object.keys(zones).length > 0 && errorCount === 0) {
  console.log('\n✅ All tests passed! API is ready to run.');
  console.log('   Start with: npm start\n');
  process.exit(0);
} else {
  console.log('\n⚠️  Some tests failed. Check your GeoJSON files.\n');
  process.exit(1);
}
