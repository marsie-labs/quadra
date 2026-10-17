---
artifact_type: strategy
id: STRAT-0002
status: Draft
---

# Strategy Document: We will bridge the collaborative gap by building a real-time event pipeline as the foundational service for all card-based interactions.

## Executive Summary

**Thesis:** We will bridge the collaborative gap by building a real-time event pipeline as the foundational service for all card-based interactions.

**From Problem Graph:** (none)

## Goals (What We Aim to Achieve)

### Goal 1: Implement a real-time event pipeline ensuring all clients reflect state changes within the 2000ms threshold.
**Why:** Required for multi-user, collaborative decision-making.  
**Success Metric:** End-to-end latency from database mutation to client rendering < 2000ms.  
**Owner:** Engineering

### Goal 2: Enable full CRUD operations for cards within the quadrant UI.
**Why:** Participants need to interact with the canvas to make decisions.  
**Success Metric:** 100% of inserted/moved cards correctly reflected across all active sessions.  
**Owner:** Engineering

## Success Criteria (Observable & Verifiable)

| Criterion | Measurement | Target | Baseline | Cadence |
|---|---|---|---|---|
| Latency from server mutation to client update. | End-to-end round-trip monitoring. | < 2000ms. | Current latency (N/A, feature not implemented). | Per release. |

## Non-Goals (What We Deliberately Skip in v1)

- **Real-time cursor tracking (mouse position).:** Adds complexity and network load without immediate benefit to decision alignment.
- **User authentication/accounts.:** We use anonymous session-based display names to maintain ephemeral, low-friction access.

## Strategic Approach (The "How")

### Core Strategy

Enable multi-user collaboration by implementing an asynchronous event-driven bridge between database mutations and client UI updates, alongside a card-centric persistence model that tracks user authorship and temporal data.

### Rationale Against Alternatives

**Why NOT Polling the database at fixed intervals for updates.:**  
Violates the core latency requirement and creates unnecessary backend load compared to event-driven push.

**Why NOT Client-side card state management with heavy local persistence.:**  
Increases risk of state divergence between participants; complicates the synchronization logic.

## Scope Boundaries (What Ships in v1)

### In Scope

- Realtime synchronization service
- Card persistence schema (quadrant, index, display name, timestamp)
- Card CRUD UI operations
- NiceGUI event handling for collaborative updates

### Out of Scope (Deferred)

- Exporting session state to external formats.
- Complex card formatting (e.g., Markdown/Rich text).

## What Could Stop This — Dependencies, Assumptions, Risks

### Dependencies

| Dependency | Owner | Status |
|---|---|---|
| Supabase Realtime API and supabase-py library support for async event subscriptions. | Supabase/Platform Engineering | Available |

### Assumptions

| Assumption | Validator | How it is checked | If false |
|---|---|---|---|
| The existing NiceGUI event loop can maintain a long-lived connection to Supabase Realtime without impacting UI responsiveness. | Lead Developer | Verification during integration testing of the Realtime service implementation. | The architecture will need to shift to a separate WebSocket gateway for Realtime events. |

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| High frequency of rapid updates during intense collaboration could saturate the event bus. | Medium | Latency spikes exceeding the 2000ms target. | Implement debouncing on client-side card movements and batching of updates where appropriate. |

## Example-First Walkthrough

### Scenario: Multi-user card movement

**Today:**
- User A sees empty quadrants.
- User B sees empty quadrants.

**With this strategy:**
- User A enters 'Alice' and creates a card in Q1.
- User B sees the new card appear in Q1 instantly with 'Alice' label and current timestamp.
- User A moves the card to Q2.
- User B sees the card move to Q2 instantly.

## Validation Checklist

- [ ] Problem Graph (DG-[ID]) is validated and reviewed
- [ ] Core strategy is one sentence (thesis)
- [ ] Goals are outcome-focused, not solution-focused (failures, not fixes)
- [ ] Success criteria are measurable and tied to business value
- [ ] Non-goals have explicit rationale (not just "v2")
- [ ] Every seriously considered direction that was rejected is recorded, with the tradeoff that decided it
- [ ] Core strategy states a direction, not a mechanism — no technology, component, signature or parameter is named
- [ ] Example-first walkthrough covers happy path + edge case
- [ ] Scope boundaries are precise (what ships, what's deferred)
- [ ] Assumptions are verifiable; risks are tracked
- [ ] Timeline is achievable; no optimistic estimation
- [ ] DRI and reviewers are aligned

## Gate 1 Review (Adversarial Requirement Review)

**Architecture Review** (verify no solution pollution):
- [ ] Strategy is outcome-focused: it names a **direction** and its consequences
- [ ] No **mechanism** is baked in — no technology, component, signature or parameter (that's SPEC's job). Choosing a direction is not pollution; choosing its implementation is
- [ ] Rejected directions are recorded with their tradeoffs, so the reviewer can attack the reasoning rather than re-derive the options
- [ ] Scope vs effort tradeoff is realistic

**Business/Product Review** (verify goals are aligned):
- [ ] Success metrics are measurable and business-relevant
- [ ] Non-goals are acceptable to stakeholders
- [ ] Timeline and investment are approved

**Approval Policy:** `(agent; human)` — Agent drafts, human (DRI + reviewers) approves before advancing to SPEC phase.

**Blockers / Rework Needed:**
- (none)

## Sign-Off

**Authored by:** google:gemini-3.1-flash-lite  
**Reviewed by:** _(pending)_ — _(pending)_

**Ready for SPEC:** [ ] Yes [ ] No
