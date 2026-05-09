"""
bin_faq.py — Georges River Council Bin Services knowledge base.

Source: https://www.georgesriver.nsw.gov.au/Services/Waste/Bin-Services

Embedded directly into the LLM system prompt (CAG) so the agent can answer
bin service FAQ questions without any tool call latency.
"""

BIN_FAQ = """
=== GEORGES RIVER COUNCIL — BIN SERVICES KNOWLEDGE BASE ===

IMPORTANT STATUS:
The Bin Collection Day Finder is currently unavailable and repairs are underway.
Residents can check their address using the Bin Collection Zone Map or contact the Waste Hotline on 1800 079 390 with their service address for assistance.

FIND YOUR BIN COLLECTION DAY:
- Printed calendar: submit a request through Log It / Fix It.
- Downloadable calendar: use the 2026 Bin Day Calendar via the Bin Collection Zone Map.
- New residents can check the Residential Waste Service Guide.

REPORT A MISSED COLLECTION OR BIN PROBLEM:
If a bin has been missed, damaged, lost, or stolen:
- Online: lodge a service request via Log It / Fix It.
- Phone: call the Waste Hotline on 1800 079 390.

BIN PLACEMENT — PRESENT BINS ON TIME:
- Place bins on the kerbside no earlier than the night before collection day.
- Return bins to the property as soon as possible after collection.

BIN PLACEMENT — SPACE REQUIREMENTS:
- Bin lid opening must face the street.
- Keep bins at least 1 metre away from parked cars, poles, or trees.
- Keep bins at least 3 metres clear from overhead lines or wires.
- Keep bins at least 30 centimetres apart from the next bin.

BIN PLACEMENT — CORRECT CONTENTS:
- Keep bin lids closed.
- Bins must not contain incorrect items or contamination.
- Two-wheeled bins must weigh under 70 kg.

BIN PLACEMENT — ALLOCATED BINS ONLY:
Old bins, foreign bins, and unauthorised unpaid bins not allocated to the property will not be collected.
Old green-bodied bins or bins provided before March 2025 should be removed by contacting the Waste Hotline on 1800 079 390.

STANDARD BIN ALLOCATION (single-unit rateable dwelling):
1. One 120L general waste bin — collected weekly.
2. One 240L recycling bin — collected fortnightly.
3. One 240L garden organics bin — collected fortnightly, alternating with recycling.

CHANGING BIN ALLOCATION:
To request new, additional, or larger bins, or to cancel bin services, residents must complete the Waste Services Application Form.
- Only the property owner or the owner's managing agent can submit the form.
- Fees and charges apply.
- Tenants must request waste services through their managing agent.

WASTE FEES AND CHARGES (2025/2026):
- Standard charge (1x120L general waste, 1x240L recycling, 1x240L garden organics): $600.00/year
- Additional volume charge (1x240L general waste, 1x240L recycling, 1x240L garden organics): $779.00/year
- Extra general waste bin 120L: $191.00/year
- Extra general waste bin 240L: $382.00/year
- Change of service administration fee: $23.50
- Extra recycling bin 240L: $135.00/year
- Extra garden organics bin 240L: $180.00/year
- Availability charge (private waste contractor or cancelled service): $82.00/year
For fee enquiries: mail@georgesriver.nsw.gov.au

DOMESTIC WASTE MANAGEMENT CHARGES:
All rateable residential properties in the Georges River LGA must pay a domestic waste management charge.
The charge covers bin collection and repair, bulky waste collection, waste disposal and processing, community recycling events, park and litter bin services, and illegal dumping collection.

CANCELLING WASTE SERVICES:
Property owners approved for development or demolition may cancel services via the Waste Services Application Form.
Once bins are removed, Domestic Waste Management Charges are removed and a Waste Availability Charge is added.

RE-APPLYING FOR WASTE SERVICES:
After an Occupation Certificate is issued, re-apply using the Waste Services Application Form.
Bins delivered within 1–3 business days after processing. Allow up to 10 business days for processing.

BIN COLLECTION TIMES:
- Collection starts from 6:00 am in most of the Georges River LGA, completed by 6:00 pm.
- Some streets have earlier collections between 4:00 am and 6:00 am for safety reasons (heavy traffic, school zones, construction).

PUBLIC HOLIDAYS:
Bins are still collected on public holidays. Vehicles may start earlier due to limited facility trading hours.

EXTREME WEATHER DELAYS:
During extreme weather (high winds, heavy rain, natural disasters), collection may be delayed.
Leave bins on the kerbside until collected if safe. Call the Waste Hotline on 1800 079 390 for assistance.

INFIRM BIN COLLECTION SERVICE:
Available for residents medically unable to wheel bins out who have no one able to assist.
Requires a completed Request for Infirm Bin Collection Service Form AND a medical certificate.
Submit to: mail@georgesriver.nsw.gov.au | PO Box 205 Hurstville BC NSW 1481 | Customer Service Centres.
Approved only after site inspection and risk assessment.

WHAT GOES IN THE GENERAL WASTE BIN (red/dark lid):
Soft plastics, plastic bags, cling wrap, plastic wrappers, bubble wrap, food scraps, polystyrene and foam,
broken glass wrapped in paper, ceramics, nappies and sanitary products, pet waste,
clothing or shoes that cannot be donated, small household items (toys without batteries, stationery, decorations, kitchenware).
NOT for large bulky items — refer to Bulky Waste Collection.

WHAT GOES IN THE RECYCLING BIN (yellow lid):
Paper and cardboard: paper (not shredded, not tissues/wipes), envelopes, pamphlets, office paper, magazines,
newspapers, wrapping paper (not foil/glitter/plastic-coated), empty cardboard (no soft plastic film),
cereal boxes, egg cartons, empty pizza boxes, flattened cardboard, toilet rolls.
Rigid plastic packaging: empty rigid plastic bottles (soft drink, water, milk, shampoo, detergent) — leave lids on;
empty rigid plastic containers (margarine tubs, yoghurt tubs, ice cream tubs, meat trays not foam, fruit punnets).
Glass: empty glass food and beverage bottles (oil, vinegar, wine, beer, soft drink) — leave lids on;
empty glass food jars (sauce, condiment, spread, spice, pickle, jam).
Aluminium and steel: empty food/drink tins and cans, tuna tins, vegetable cans, biscuit tins, milk formula tins;
empty aerosol spray cans (cooking oil, deodorant, air freshener, bug spray, hair spray) — no compressed gas canisters;
empty aluminium packaging (drink cans, foil trays, aluminium foil scrunched into balls).
For uncertain items use Recycle Mate or the A-Z Recycling Guide.

WHAT GOES IN THE GARDEN ORGANICS BIN (lime green lid):
Grass clippings, leaves and twigs, small branches (max 1 metre long and 15 cm wide), flowers, weeds and cuttings.
Tips: bins must not exceed 70 kg; leave lid slightly open to let moisture out; dry damp materials before adding;
place sticks at the bottom to stop grass clippings sticking; split large amounts across two collections.

WHERE DOES WASTE GO:
- General waste: Veolia Woodlawn Mechanical and Biological Treatment facility.
- Recycling: VISY Smithfield Recycling Material Recovery Facility.
- Garden organics: Cleanaway Lucas Heights Resource Recovery Park (commercially composted).

BIN TAGS:
- Green tag: bin contains correct items.
- Red tag: incorrect items found — tag will specify the problem.
- Non-collection sticker: bin is too heavy, overflowing, or heavily contaminated.
  Resolve the issue then call 1800 079 390 to arrange collection.

CONTACT DETAILS:
- Waste Hotline: 1800 079 390 (Mon–Fri 8:30 am–5:00 pm)
- Council phone: (02) 9330 6400
- Email: mail@georgesriver.nsw.gov.au
- Hurstville Civic Centre: Corner MacMahon and Dora Streets, Hurstville (Mon–Fri 8:30 am–5:00 pm)
- Kogarah Service Centre: Kogarah Town Square, Belgrave Street, Kogarah (Mon–Fri 9:00 am–5:00 pm)
"""
