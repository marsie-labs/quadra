---
artifact_type: architecture
id: ARCH-0001
version: 1.1
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
quadra_system -->|Persists session/quadrant data and triggers Realtime broadcasts.| supabase
supabase -->|Pushes Realtime change events on sessions and quadrants; quadra-app forwards them to browsers over NiceGUI's WebSocket.| quadra_system
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
quadra_app -->|Persists session/quadrant data and triggers Realtime broadcasts.| supabase
supabase -->|Pushes Realtime change events on sessions and quadrants; quadra-app forwards them to browsers over NiceGUI's WebSocket.| quadra_app
```

### Component: NiceGUI Application

```mermaid
flowchart LR
classDef added fill:#c8f7c5,stroke:#2e7d32
classDef changed fill:#fff3b0,stroke:#b59f00
classDef removed fill:#f8c8c8,stroke:#c62828
subgraph quadra_app["NiceGUI Application"]
  session_service["Session Management Service<br/>component"]
end
supabase["Supabase<br/>system"]
session_service -->|Persists session/quadrant data and triggers Realtime broadcasts.| supabase
```

| ID | Unit | Level | Parent | Owns | Does NOT own | Deployment | Tech Family |
|----|------|-------|--------|------|--------------|------------|-------------|
| quadra-system | Quadra System | system |  | Coordinates real-time decision-making via collaborative quadrants. |  |  |  |
| quadra-app | NiceGUI Application | container | quadra-system |  |  |  | FastAPI, NiceGUI, Python |
| session-service | Session Management Service | component | quadra-app |  |  |  | Python module, supabase-py |
| supabase | Supabase | system |  | PostgreSQL storage and Realtime sync engine. |  |  |  |

## 9. Communication Patterns

| From | To | Description |
|------|----|-------------|
| quadra-app | session-service | Calls create_session in-process to handle POST /sessions. |
| session-service | supabase | Persists session/quadrant data and triggers Realtime broadcasts. |
| supabase | quadra-app | Pushes Realtime change events on sessions and quadrants; quadra-app forwards them to browsers over NiceGUI's WebSocket. |

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
