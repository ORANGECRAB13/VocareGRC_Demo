# Utilities10x Demo: Power Outage + Council + Utility Coordination

## Caller

> My street has been without power for six hours. Why hasn't it been fixed yet,
> and when will it be back?

The caller is Sarah Chen at 42 River Street, Riverstone. The demo assumes the
inbound account has already identified her address, so Ava can answer directly.

## What a traditional voice AI does

It checks an outage API and reads the status:

> There is an outage affecting your area. Crews are investigating.

That confirms the obvious but does not explain the delay, the current blocker,
or what happens next.

## What a human agent would need to do

The human would manually move between:

- Outage management
- Asset registry and maintenance history
- Field crew dispatch
- Council roadworks records
- Traffic-control permit approvals
- Contractor notes
- Restoration workflow and escalation procedures

They may also need to call another team to discover which dependency is blocking
the repair. This can easily take 10–20 minutes.

## What Ava should say

> A damaged underground cable caused the outage, but council roadworks delayed
> excavation until the traffic-control permit was approved at 11:27. The
> contractor and repair crew are now onsite, with power currently estimated
> back by 4:30 PM.

The exact wording can vary, but the first response should remain two short
sentences. Timeline, asset history, escalation, and workflow details are
reserved for follow-up questions.

## Authoritative demo timeline

| Time | Event |
|---|---|
| 8:14 AM | Protection equipment isolates a damaged underground cable |
| 9:03 AM | Utility crew E-27 arrives and confirms the cable fault |
| 9:18 AM | Traffic-control permit request is lodged with council |
| 10:42 AM | Utility restoration coordinator escalates the permit |
| 11:27 AM | Council traffic-control team approves the permit |
| Now | MetroSafe is onsite establishing the approved lane closure |
| 4:30 PM | Current estimated restoration time |

## Connected context behind the answer

### Utility systems

- Outage management: 38 affected properties and outage start time
- Asset registry: damaged underground cable LV-RS-204
- Crew dispatch: crew arrival, diagnosis, and current onsite status
- Asset history: two moisture inspections and planned replacement next month

### Council systems

- Active western-lane resurfacing works
- Excavation and traffic-control permit
- Approval timestamp and approving team
- Approved lane-closure plan

### Contractor context

- MetroSafe traffic contractor assignment
- Contractor arrival and lane-closure status

### Workflow context

- The traffic-control permit was the blocking restoration step
- The permit is now complete
- Remaining steps: lane closure, excavation, cable splice, electrical testing,
  and re-energisation
- Six-hour escalation is open with the network duty manager

## The graph story

```text
Outage
├── Customer / affected properties
├── Damaged cable asset
│   ├── Inspection history
│   └── Planned replacement
├── Utility repair crew
├── Council roadworks
│   └── Traffic-control permit
│       ├── Escalation
│       └── Approval
├── Traffic contractor
├── Restoration workflow
│   └── Current blocking step
└── Restoration estimate and escalation
```

A normal assistant sees separate records. The demonstration shows an agent
reasoning over the relationships among the outage, asset, people, approvals,
work dependencies, and expected next action.

## Suggested follow-up questions

- Why did the permit take so long?
- Are the repair crews actually onsite?
- What exactly happens after traffic control is set up?
- Has this cable failed before?
- Who approved the permit?
- Is 4:30 guaranteed?
- What happens if the estimate slips again?

## Presenter reveal

After the voice interaction, explain:

> The useful part was not simply retrieving an outage record. The answer depended
> on identifying the relationship between a damaged utility asset, a field crew,
> active council roadworks, a traffic-control approval, a contractor, and the
> restoration workflow. That connected operational context is the graph.
