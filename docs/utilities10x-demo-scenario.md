# Utilities10x Demo Scenario

## Recommended caller scenario

> Hi, our power flickered a few times and now it is completely out at 18 Wattle
> Grove in Riverstone. The outage map says there is nothing in our area, my
> next-door neighbour still has power, and my mum uses an oxygen concentrator.
> Can you tell me what is happening and how long this is likely to take?

This query is short, but answering it well requires joining facts that would
normally be fragmented across several utility systems:

- Customer and life-support registration
- Smart-meter voltage and last-gasp telemetry
- Low-voltage network topology
- Public outage-map publishing thresholds
- Council roadworks and cable-strike reports
- Crew dispatch and estimated arrival
- Switching options and repair estimates

## Demo facts

- Address: 18 Wattle Grove, Riverstone NSW
- Customer: Sarah Chen, account ending 4821
- Registered life-support property; oxygen concentrator battery lasts roughly
  45 minutes
- Voltage sags began at 4:03 PM and supply was lost at 4:07 PM
- Eleven properties share the same outage signature
- The public map is blank because the cluster is below its publishing threshold
- The neighbour is connected to a different low-voltage spur
- A roadworks contractor reported a suspected underground cable strike at 4:05 PM
- Crew E-27 is approximately 12 minutes away
- Nine properties may be restored by switching in 25–35 minutes
- This address is on the damaged section, with a current estimate of 2–3 hours

## What makes the response compelling

The agent should not dump those facts. It should synthesize them into something
like:

> This looks like a small local cable fault rather than a wider feeder outage,
> which explains why the public map is blank and your neighbour still has power.
> A crew is about 12 minutes away, but your property appears to be on the damaged
> section, so the current estimate is two to three hours. Because your mum uses an
> oxygen concentrator, switch to the backup battery now; if it may not last or she
> has any breathing difficulty, call Triple Zero immediately.

## Suggested follow-up questions

- Why does my neighbour still have power?
- Why is the outage missing from the map?
- Can they restore us by switching the network?
- Is the two-to-three-hour estimate guaranteed?
- What should I do if the oxygen battery only has 20 minutes left?
- What information was connected to work that out?

The final question allows the presenter to explain the context-graph vision
after the voice interaction without requiring the agent to expose internal
system names during the normal call.
