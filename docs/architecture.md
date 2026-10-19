---
artifact_type: architecture
id: ARCH-0001
version: 1.2
status: Approved
---

# ARCHITECTURE.md

## 1. System Mission

Quadra facilitates real-time team alignment by providing an ephemeral, highly responsive workspace where participants can co-create and manage quadrant-based decisions.

## 2. Business Goals

To provide a standardized, reliable, and real-time collaboration environment for team decision-making, specifically optimized for the rapid instantiation of workspace sessions.

## 3. Architectural Drivers

(none yet)

## 4. Architectural Style

The system follows a reactive, event-driven architecture. The client-side UI (NiceGUI) interacts with a lightweight FastAPI controller that delegates session persistence to Supabase. Supabase acts as both the primary database and the real-time event bus, ensuring that changes to the quadrant state are propagated to all connected clients within the 2000ms latency requirement.

## 5. Quality Attribute Scenarios

(none yet)

## 6. Tactics

(none yet)

## 7. Architectural Patterns

(none yet)

## 8. System Decomposition

The system consists of the NiceGUI-based web application, a session management service, and the external Supabase platform. Communication is centered around the Supabase Realtime engine, which synchronizes state between the backend and all active clients.

### Context

```mermaid
flowchart LR
classDef added fill:#c8f7c5,stroke:#2e7d32
classDef changed fill:#fff3b0,stroke:#b59f00
classDef removed fill:#f8c8c8,stroke:#c62828
quadra_system["Quadra System<br/>system"]
supabase["Supabase<br/>system"]
quadra_system -->|"Persists session/quadrant data and triggers Realtime broadcasts."| supabase
quadra_system -->|"Reads and writes session-scoped card records using supabase-py; committed mutations are the source of Realtime events."| supabase
supabase -->|"Pushes committed card changes and existing session/quadrant change events over asynchronous Supabase Realtime subscriptions, without polling."| quadra_system
```

### Container

```mermaid
flowchart LR
classDef added fill:#c8f7c5,stroke:#2e7d32
classDef changed fill:#fff3b0,stroke:#b59f00
classDef removed fill:#f8c8c8,stroke:#c62828
subgraph quadra_system["Quadra System"]
  quadra_app["NiceGUI Application<br/>container"]
end
supabase["Supabase<br/>system"]
quadra_app -->|"Persists session/quadrant data and triggers Realtime broadcasts."| supabase
quadra_app -->|"Reads and writes session-scoped card records using supabase-py; committed mutations are the source of Realtime events."| supabase
supabase -->|"Pushes committed card changes and existing session/quadrant change events over asynchronous Supabase Realtime subscriptions, without polling."| quadra_app
```

### Component: NiceGUI Application

```mermaid
flowchart LR
classDef added fill:#c8f7c5,stroke:#2e7d32
classDef changed fill:#fff3b0,stroke:#b59f00
classDef removed fill:#f8c8c8,stroke:#c62828
subgraph quadra_app["NiceGUI Application"]
  session_service["Session Management Service<br/>component"]
  card_service["Card Service<br/>component"]
  realtime_service["Realtime Synchronization Service<br/>component"]
  collaborative_canvas["Collaborative Canvas<br/>component"]
end
supabase["Supabase<br/>system"]
session_service -->|"Persists session/quadrant data and triggers Realtime broadcasts."| supabase
collaborative_canvas -->|"Calls in-process to load session cards and request insert, update, soft-delete and relocation operations."| card_service
card_service -->|"Reads and writes session-scoped card records using supabase-py; committed mutations are the source of Realtime events."| supabase
collaborative_canvas -->|"Registers and unregisters connected session views for push delivery as clients connect and disconnect."| realtime_service
supabase -->|"Pushes committed card changes and existing session/quadrant change events over asynchronous Supabase Realtime subscriptions, without polling."| realtime_service
realtime_service -->|"Dispatches session-scoped database changes in-process; the canvas updates connected browsers through NiceGUI's WebSocket without polling."| collaborative_canvas
```

| ID | Unit | Level | Parent | Owns | Does NOT own | Deployment | Tech Family |
|----|------|-------|--------|------|--------------|------------|-------------|
| quadra-system | Quadra System | system |  | Coordinates real-time decision-making via collaborative quadrants. |  |  |  |
| quadra-app | NiceGUI Application | container | quadra-system |  |  |  | FastAPI, NiceGUI, Python |
| session-service | Session Management Service | component | quadra-app |  |  |  | Python module, supabase-py |
| supabase | Supabase | system |  | PostgreSQL storage and Realtime sync engine. |  |  |  |
| card-service | Card Service | component | quadra-app | Loads session-scoped cards and persists insert, update, soft-delete and quadrant relocation operations, including ordering, anonymous display-name authorship and timestamps. | User authentication, durable browser-side state or delivery of Realtime events to connected clients. |  | Python module, supabase-py |
| realtime-service | Realtime Synchronization Service | component | quadra-app | Maintains asynchronous Supabase Realtime subscriptions within the NiceGUI event loop, routes database change notifications to connected views of the affected session, and manages subscription lifecycle and cleanup. | Card persistence, cursor tracking or a separate WebSocket gateway. |  | Python, supabase-py async Realtime API |
| collaborative-canvas | Collaborative Canvas | component | quadra-app | Renders session cards, captures anonymous display names and card operations, provides optimistic or quick-confirm feedback for the initiating participant, and applies confirmed changes to connected browser views through NiceGUI. | Authoritative card storage, user accounts or direct browser subscriptions to Supabase. |  | Python, NiceGUI |

## 9. Communication Patterns

| From | To | Description |
|------|----|-------------|
| quadra-app | session-service | Calls create_session in-process to handle POST /sessions. |
| session-service | supabase | Persists session/quadrant data and triggers Realtime broadcasts. |
| collaborative-canvas | card-service | Calls in-process to load session cards and request insert, update, soft-delete and relocation operations. |
| card-service | supabase | Reads and writes session-scoped card records using supabase-py; committed mutations are the source of Realtime events. |
| collaborative-canvas | realtime-service | Registers and unregisters connected session views for push delivery as clients connect and disconnect. |
| supabase | realtime-service | Pushes committed card changes and existing session/quadrant change events over asynchronous Supabase Realtime subscriptions, without polling. |
| realtime-service | collaborative-canvas | Dispatches session-scoped database changes in-process; the canvas updates connected browsers through NiceGUI's WebSocket without polling. |

## 10. Data Architecture

(none yet)

## 11. Cross-cutting Concerns

(none yet)

## 12. Deployment Topology

(none yet)

## 13. Global Anti-Patterns

(none yet)

## 14. Open Architectural Risks

Realtime push is deferred. SPEC-0001 describes Supabase Realtime events pushed to every connected client through NiceGUI's WebSocket without polling, and the supabase to quadra-app relation in section 9 records it, but SPEC-0001's PBIs build only session creation and the canvas page. Until a later spec builds it, nothing proves that supabase-py's async Realtime client runs inside NiceGUI's event loop and meets STRAT-0001's < 2000ms target, which is the reason NiceGUI was chosen. The spec for Intent 2 (card operations) should build Realtime first, in its own PBI, before any card UI relies on it.

## 15. Architecture Decision Index

(none yet)

## 16. Related Artifacts

(none yet)

## Changelog

| Version | Date | Summary | Triggered by |
|---------|------|---------|--------------|
| 1.0 | 2026-10-16 | (no summary) | SPEC-0001 |
| 1.1 | 2026-10-16 | Record the deferral of Realtime push as an open risk | SPEC-0001 PBI review |
| 1.2 | 2026-10-17 | Add card persistence and an asynchronous Supabase-to-NiceGUI event bridge, with explicit cross-client push acceptance criteria under 2000ms. | SPEC-0002 |
