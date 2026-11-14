# Real-world scenarios — how each is handled

Every situation the business meets that needs a task, a ticket or a follow-up,
and how the system handles it. Checked against the code on 13 Nov 2026
(read-only review). **Yes** = handled; **Partly** = handled but the follow-up
isn't reliable; **Gap** = not handled. Each Partly/Gap is an item in
`docs/hardening_plan.md` §3C; when it's built, this page is updated.

"Follow-up ticket" = the internal task the system opens by itself when a
receiving, loading or installation finds machines missing, damaged or wrong
(`tasks/equipment.py` `open_equipment_follow_up`).

## A. From the factory to us

| Scenario | Today | Missing for a reliable follow-up |
|---|---|---|
| Container arrives at a warehouse | Yes — receiving task, code × quantity, adds to stock | — |
| Arrives at a **showroom** | Partly — no showroom kind; a showroom counts as a warehouse | Warehouse / showroom kind on our sites |
| Missing / damaged / wrong model | Yes — opens the follow-up ticket | The follow-up isn't linked, dated or reminded (C1) |
| Wrong colour | Gap — colours just retyped | Received ≠ planned colour → follow-up line |
| Too many arrived | Partly — quantity raised, nobody told | Surplus → follow-up line |
| Serial unreadable at installation | Partly — can't be marked done | "No readable plate" option, as on visits |
| Delivery note / invoice missing | Partly — optional files | Receiving needs its delivery note, or opens a follow-up |
| **Claim to the factory until credit/replacement** | Partly — the follow-up ticket's text only | Claim number, waiting state, due date, reminders, closing result (C1) |
| Replacement machine received | Partly — new receiving | Linked to the claim, closes it |
| Return to the factory | Gap | Send machines out of stock with a reason |

## B. Showroom and demo machines

| Scenario | Today | Missing |
|---|---|---|
| Display machines | Partly | Showroom kind |
| Demo lent to a customer and returned | Gap | A loan with a due-back date and reminder; the return back into stock |
| **Showroom machine sold to a customer** | Gap — the system says "move it on the customer's page", which doesn't exist | "Move to another site" on the machine (C4) |
| Showroom machine servicing | Yes | — |

## C. Warehouse

| Scenario | Today | Missing |
|---|---|---|
| Stock count and difference | Partly — recorded with a note | A large shortfall tells the manager |
| Damaged in storage | Gap | A count reason that opens a follow-up |
| Loading mistakes | Partly — **a machine left behind still counts as out of stock** | Count only loaded lines out (C3) |
| Planned beyond stock | Yes — with a recorded reason | — |
| Transfer between warehouses / countries | Gap | A loading sent on to another warehouse's receiving |
| Loaded but not installed | Partly — a list on Stock | In the bell after N days |

## D. Delivery and installation

| Scenario | Today | Missing |
|---|---|---|
| Site not ready / access | Partly — readiness check, warning, blocked + rebook, alarms | A reminder to check readiness before the date |
| Damaged in transit to the customer | Partly — **not carried to the follow-up installation** | Damaged / wrong lines wait for their replacement (C2) |
| Partial installation | Yes, once | A second follow-up (C2) |
| Customer refuses delivery | Partly — "returned" | Back into stock; someone told (C3) |
| Handover and training | Yes | Training record (optional) |
| Snag list | Partly — "not resolved" is never acted on | Opens a follow-up to the owner (C6) |
| Corrections after the first report | Gap | Follow-ups and machines on every save (plan 3.4) |

## E. After-sales

| Scenario | Today | Missing |
|---|---|---|
| Breakdown | Yes | — |
| Repeat fault on the same machine | Partly — machine history | "3rd visit in 90 days" flag |
| Warranty vs chargeable | Yes | — |
| **Parts ordered and late** | Partly — no alarm when the expected date passes | Late parts in the bell (C5) |
| Wrong part received | Partly | Linked to the repair |
| Part shipment | Yes | — |
| **Quotation and customer approval** | Gap — a file only | Sent / approved / refused, reminder (C7) |
| **PPM contracts** | Gap — each visit by hand | Interval per branch; the next visit made when one closes (C7) |
| Emergency callout | Yes | — |
| **Safety incident / injury** | Gap | Incident record, immediate escalation, machine out of use |
| Upholstery, calibration, inspection | Yes | — |
| Relocation | Partly | Machines move sites (C4) |
| Decommission / removal / trade-in | Gap — statuses never set | "Retire" on the machine (C4) |

## F. Customer side

| Scenario | Today | Missing |
|---|---|---|
| Complaint | Partly — "Something else" + escalation | A complaint category; **low ratings tell nobody** (C6) |
| Dispute about a report | Partly | Linked to the report |
| Unpaid invoice / billing | Out of scope — accounting outside the system | — |
| Customer adds a machine | Yes | Someone told (plan 3.10) |
| New branch | Yes | — |
| Customer closes a branch | Gap | A branch can be closed |
| Ownership change | Gap | A branch moves to another customer |
| Contact leaves | Yes | — |

## G. Internal

| Scenario | Today | Missing |
|---|---|---|
| Technician sick mid-job | Yes | — |
| Vehicle / tools | Partly | — (no register, by design) |
| Training | Yes | — |
| Supplier / vendor follow-up | Partly — internal task | Due date and reminder (C1) |
| Subcontractor (flooring) | Gap | Plan 3.12 |
| Quality audit of a job | Partly | Low ratings trigger a review (C6) |

## H. Other

| Scenario | Today | Missing |
|---|---|---|
| Factory recall / safety notice | Gap | Find every machine by model/serial; a task per branch |
| Warranty ending | Gap | A reminder before it ends |
| Customer's own team fixes, we ship parts | Yes | — |
| Visit cancelled from a request | Gap | Plan 3.6 |
