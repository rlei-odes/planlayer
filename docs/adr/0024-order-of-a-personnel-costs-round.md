# 0024: The order of a personnel costs round

- **Status:** Accepted
- **Amends:** ADR 0022 (HR's review), ADR 0023 (release)
- **Date:** 2026-11-15

## Context

With personnel costs in a round, three parties act on pay: the controller, who opens the round; HR, who decides access and prepares the pay; and the people with access, who plan it. Without a fixed order, HR could release access or confirm pay in a round the controller hadn't opened. The people with access could also start changing salaries HR hadn't finished loading.

## Decision

### Three steps, each unlocking the next

| Step | Who | Per | Possible when | Unlocks |
|---|---|---|---|---|
| 1. Open the round | controller | round | the round is in setup | HR's release, handovers and confirmations |
| 2. Release access control | HR | round | the round is open | the people with access see their cost centers' pay, read only |
| 3. Hand pay over | HR | cost center | the round is open and access released | the people with access edit that cost center's pay |

- **Preparing comes before the steps.** While the round is in setup, HR already keeps the rules and the access lists, and loads or types salaries.
- **Handover** (`pay_handover`): until HR hands a cost center's pay over, the store refuses pay changes by anyone but HR, and the page tells the people with access that HR is preparing it. HR can take a handover back.
- **The other sheets don't wait.** The cost center's costs, capacity and workforce follow the workflow status as before. The personnel cost totals on the cost sheet show HR's figures until the pay is handed over and changed.

### The review before approval is optional

- HR's review (ADR 0022) stays the last step: after the people with access have changed pay, HR confirms it, and only then can the cost center be approved.
- HR can switch it off for a round on the *Rules* tab (`pay_setting.review_required`, on by default). Then approval doesn't wait for HR; the handover still does.
- HR confirms only in an open round.

### Guidance

HR's *Home* shows, per open round, the next step: *Release access control*, then *Hand over pay* with the cost centers still waiting. The review and small groups follow as before. The personnel costs page shows the handover and the review per cost center, each with HR's button.

### Enforcement

- A trigger refuses a release, a handover or a confirmation by anyone but HR, and in a round that isn't open; a handover also needs the release. Taking them back stays possible.
- The lock on pay (ADR 0022) refuses changes by others than HR and the system user to the pay of a cost center not handed over.
- Handovers and their withdrawal are written to the access log (ADR 0023).

## Reasons

- **The controller opens first:** HR's steps act on a round whose scope and setup are final. Before that, HR prepares without anyone else seeing anything.
- **Access before handover:** the people with access can see what HR is preparing and plan around it, without editing yet.
- **Handover per cost center:** HR's work arrives cost center by cost center (a department's list is checked, the next one loaded), and planners can start where it's done.
- **An optional review:** some companies want HR to see every change before approval; others trust the handover and leave approval to the approvers.

## Consequences

- New table `pay_handover`; `pay_setting.review_required`. A trigger on `pay_release`, `pay_handover` and `pay_review` checks HR and the open round.
- The access log gains `handed_over` and `handover_withdrawn`.
