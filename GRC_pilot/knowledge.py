"""
knowledge.py — Full FAQ and policy knowledge scraped from:
https://www.georgesriver.nsw.gov.au/Services/Waste/Bulky-Waste-Collection

This is injected verbatim into the agent's system prompt so it can answer
all resident questions without latency from a retrieval step.
"""

BULKY_WASTE_KNOWLEDGE = """
=== GEORGES RIVER COUNCIL — BULKY WASTE COLLECTION: FULL KNOWLEDGE BASE ===

OVERVIEW
--------
When residents have unwanted items that cannot be reused, donated, or sold,
they can book a Bulky Waste Collection through Georges River Council.

Residents are entitled to book up to TWO (2) Bulky Waste Collections of
3 cubic metres (3m × 1m × 1m) per property per calendar year. Unused
allocations CANNOT be carried over to the following year.

Multi-unit dwellings of SIX (6) or more units must organise their collection
through their Strata or Body Corporate.


BOOKING PROCESS
---------------
Residents can book a Bulky Waste Collection:
  • Online via the Council's online booking platform
  • Speaking with this voice agent (you)

You (the voice agent) can book on behalf of the resident. You will need:
  1. Full name
  2. Phone number
  3. Property address (within the Georges River LGA)
  4. Preferred collection date

Multi-unit dwellings of 6 or more units must contact their Strata or Body
Corporate — the voice agent cannot process these bookings.


PLANNING & WAIT TIMES
----------------------
• Book at least 2 weeks in advance of the preferred collection date.
• Online platform: book up to 10 weeks in advance.
• Phone/voice booking: up to 16 weeks in advance.
• Wait times: generally 1–2 weeks between late April and October.
• Peak period wait times (e.g. January–April): up to 8 WEEKS.
• The preferred date may not always be available.


WHAT IS ACCEPTED IN A BULKY WASTE COLLECTION
----------------------------------------------
✅ ACCEPTED ITEMS:
  • Furniture: lounges, tables, chairs, shelves, garden furniture, bed frames
  • Mattresses and ensemble bed bases (max 2 per 3m³ booking; must be booked
    separately and placed beside — not under — other items; collected by a
    separate mattress recycler on the same day)
  • Fridges and freezers (doors MUST be removed; must be booked separately
    and placed beside other items; degassed by a separate vehicle)
  • White goods: washing machines, dryers, dishwashers, microwave ovens,
    ovens, stoves
  • Garden vegetation: tied and bundled; branches no wider than 15 cm
    diameter; cut into ~1 m lengths; tied with natural rope, string,
    boxed or bagged
  • Bikes and scooters (NO batteries)
  • Miscellaneous household items (boxed, bagged or bundled): decorations,
    racks, pans, toys (no batteries), pots, garden equipment (no batteries,
    empty), sporting goods
  • Small amounts of timber and fence palings (nails removed), cut into 1 m
    lengths, no more than 0.5m × 1m × 1m in volume
  • Carpets and rugs: less than 3 m in length, rolled and tied; no trade
    waste; no more than 0.5m × 1m × 1m in volume

❌ NOT ACCEPTED ITEMS:
  • Hazardous, chemical or liquid waste: batteries (household or car),
    gas bottles, paint, oil, medical waste
    → Refer to Council's Household Chemical CleanOut program or A-Z Guide
  • Electrical waste (E-Waste): televisions, games consoles, set top boxes,
    computers, mobile phones, devices with embedded batteries
    → Refer to E-Waste & Extras Drop-Off or A-Z Guide
  • Building, trade and industrial waste: bricks, cement, roof insulation,
    asbestos, tiles, sand, stones, soil, roller shutters, large carpet rolls
    (>3m), bath/laundry tubs, solar panels, air conditioners
  • Car or motor vehicle parts (e.g. tyres)
  • Glass: plate or sheet glass, shower screens, windows, mirrors,
    glass tabletops
  • Large tree trunks and branches over 15 cm in diameter; thorny bushes;
    spiky prunings (e.g. palm fronds, cacti, succulents)
  • General waste: food waste, nappies, small loose/spillable items
    → Use the general waste bin
  • Recyclable items: loose paper, cardboard, bottles, cans
    → Use the recycling bin
  • Loose vegetation: leaves and grass
    → Use the garden organics bin
  • Heavy items that three people are unable to lift
  • Oversized items more than 2 m in length or not suitable for vehicle
    compaction
  • Excess material more than 3 cubic metres (unless using both entitlements
    for a 6m³ single booking)

Council reserves the right to reject items that do not comply with Terms
and Conditions. Visit the A-Z Recycling Guide for disposal alternatives.


HOW MUCH CAN BE PLACED OUT
---------------------------
Each property is entitled to:
  OPTION A: Two (2) separate bookings of 3 cubic metres (3m³) each,
            on two different dates in the calendar year.
  OPTION B: One (1) booking of 6 cubic metres (6m³), using BOTH
            entitlements at once.

Materials must not exceed 3m³ for a single booking unless the resident
chooses Option B to use both entitlements at once.


HOW TO PRESENT ITEMS FOR COLLECTION
-------------------------------------
• Place only accepted items on the kerbside the NIGHT BEFORE the collection
  date — NOT earlier (early placement is treated as Illegal Dumping).
• Items must be neat, not spilling onto the road. Avoid obstructing the
  footpath and driveways. Never place items on the road or in gutters.
• Mattresses: place to the SIDE of other bulky items (not under them).
  They are collected by a separate recycling contractor.
• Fridges/freezers: place beside other bulky items with doors removed.
• If your collection is not directly in front of your property,
  note the location (e.g. side of property, rear laneway) in the booking.


AFTER BOOKING — WHAT HAPPENS NEXT
------------------------------------
• After booking, the resident receives a booking reference number.
• A reminder will be sent one week before the collection date.
• If no confirmation email/text arrives within an hour, check junk mail.


TERMS AND CONDITIONS — KEY RULES
----------------------------------
1. EARLY PLACEMENT: Only place items out the NIGHT BEFORE the booked date.
   Placing items out earlier is Illegal Dumping and significant fines apply.

2. NON-PRESENTATION: If items are not placed out, the booking is still
   counted as one or both of the resident's allocated collections.

3. CANCELLATIONS AND CHANGES: Must be made at least TWO (2) BUSINESS DAYS
   before the booking date. Failure to do so forfeits the collection.
   Only ONE change of date or cancellation of the original booking is
   permitted. If items are not placed out after the second booking date,
   the booking is forfeited.

4. EXCESS MATERIALS:
   • Exceeds 3m³ but within 6m³ → both entitlements will be used.
   • Exceeds 6m³ or second booking with no remaining entitlements
     → excess materials WILL NOT be collected.
   • Unacceptable materials will not be collected.
   • Any materials not collected remain the RESIDENT's responsibility.
   • Fines of $2,000+ apply for Illegal Dumping under the Protection of
     the Environment Operations Act 1997.

5. FRIDGE / FREEZER: Doors must be removed. Must be booked in advance and
   placed beside other items for separate degassing vehicle collection.

6. MATTRESSES: Must be booked in advance.
   Max 2 per 3m³ booking; max 4 per 6m³ booking.


FREQUENTLY ASKED QUESTIONS
-----------------------------

Q: Do I need to plan ahead for a booking?
A: Yes. Book at least 2 weeks in advance. Wait times are 1–2 weeks between
   late April and October. During peak periods, wait times can be up to
   8 weeks. You can pre-schedule up to 16 weeks in advance via the
   voice agent.

Q: What do I do after booking?
A: You'll receive a booking reference number. Place accepted items neatly on
   the kerbside the night before the collection date. You'll receive a
   reminder one week prior to your date.

Q: How do I cancel my collection?
A: You can cancel or change via this voice agent. You must cancel at least
   2 business days before the booking date. If insufficient notice is given,
   the collection is forfeited. Only one change is allowed; if items aren't
   placed out for the rescheduled date, the booking is forfeited.

Q: I am moving house. How do I access a collection?
A: Book well in advance — peak period waits can exceed 8 weeks. If no
   bookings are available or you've used your entitlements, you will need
   to arrange private waste removal. See the 'No collections remaining' FAQ.

Q: I have no Bulky Waste Collections remaining. What can I do?
A: Options include:
   • Check Council's Reuse, Repair and Recycle page and A-Z Recycling Guide.
   • Use the Recycling Near You database or Recycle Mate app.
   • Contact the Bower Reuse & Repair Centre or their Reuse Database.
   • Ask local op shops and charities if they accept items for resale.
   • Engage a reputable private waste removalist or skip bin service.
   Note: Do NOT leave materials on public land without a booking — Illegal
   Dumping fines of $2,000+ apply.

Q: What if I live in a business or non-rateable property?
A: This service is for DOMESTIC properties only. Commercial/business-rated
   properties are NOT eligible. If renting at such a property, speak with
   your real estate agent or property manager.

Q: I have a missed collection (mattress, fridge, or general bulky waste)?
A: Mattresses and fridges are collected by separate contractors at a
   different time. They must be accessible and placed to the side — not under
   other items. If they were not collected due to non-compliance with Terms
   (e.g. not booking a mattress or fridge), they remain your responsibility.
   You must remove them immediately (Illegal Dumping fines may apply) and
   either use a remaining allocation to rebook or dispose of at your own cost.

Q: Why did the booking system change from a scheduled date?
A: In 2020, the community provided feedback preferring an on-call booked
   system over a pre-scheduled one. The booking system allows residents to
   choose dates that suit them, unlike the former Kogarah Council's
   pre-scheduled clean ups.

Q: I received a non-compliance letter. What do I do?
A: Review the letter and follow the instructions. Materials listed in the
   letter that were unacceptable or uncollected must be removed immediately.
   Do not leave materials on public land — significant fines apply.

   If excess materials were collected beyond your booking:
   • Up to 6m³ collected on a single booking → both entitlements used (none
     remaining for the year).
   • Materials exceeding 6m³ or a second booking with no entitlements →
     excess materials will not be collected.


ADDITIONAL RESOURCES (for reference only — agent cannot browse these)
----------------------------------------------------------------------
• A-Z Recycling Guide: georgesriver.nsw.gov.au/Services/Waste/A-Z-Recycling-Guide
• Reduce, Reuse, Recycle: georgesriver.nsw.gov.au/Services/Waste/Refuse-Reduce-Repair-Reuse-Resell-Recycle
• Household Chemical CleanOut: georgesriver.nsw.gov.au/Services/Waste/Household-Chemical-CleanOut
• E-Waste Drop-Off: georgesriver.nsw.gov.au/Services/Waste-en/E-Waste-Drop-Off
• Strata Managers info: georgesriver.nsw.gov.au/Services/Waste/Strata-Managers-and-Unit-Development
• Illegal Dumping: georgesriver.nsw.gov.au/Services/Waste-en/Illegal-Dumping
• Bin Services: georgesriver.nsw.gov.au/Services/Waste/Bin-Services
"""
