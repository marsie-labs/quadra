---
artifact_type: context map
id: CTXMAP-quadra
scope: project
status: Generated
generated_from: b998d66d793b49da147662c959e8d74ba22a7f65
---

# Context Map: quadra

## Index

### Repositories

| Repository | Role | Architecture |
|------------|------|--------------|
| git@github.com:marsie-labs/quadra.git |  |  |

### Units

| Unit | Kind | Location | Depends on |
|------|------|----------|------------|
| Quadra System (quadra-system) | system | docs/architecture.md#8-system-decomposition |  |
| NiceGUI Application (quadra-app) | container | docs/architecture.md#8-system-decomposition | Session Management Service |
| Session Management Service (session-service) | component | docs/architecture.md#8-system-decomposition | Supabase |
| Supabase (supabase) | system | docs/architecture.md#8-system-decomposition | NiceGUI Application |

## Glossary

| Term | Context | Means | Owned by |
|------|---------|-------|----------|
|  |  |  |  |

## Routing Table

| If the work concerns | Go to | Kind |
|----------------------|-------|------|
| Workspace and Realtime Canvas Initialization (SPEC-0001) | docs/specs/SPEC-0001.md | spec |
| Use UUIDv4 for Session IDs (ADR-0001) | docs/adrs/ADR-0001.md | ADR |
| Enforce Feature Name Validation (ADR-0002) | docs/adrs/ADR-0002.md | ADR |
| Architecture (ARCH-0001) | docs/architecture.md | architecture |

## Validation Checklist

**Mechanical**

- [ ] Every pointer in every section resolves to a file, and an anchored
      pointer resolves to a heading that exists
- [ ] No routing entry points at a directory, at another index, or at a
      file whose answer is a further redirect (the two-lookup rule)
- [ ] Every spec and every ADR owned at this scope is reachable from the
      Routing Table
- [ ] The Index is complete for this scope: every project (org), every
      mapped repository (project), or every unit (repo)
- [ ] Only the tables this scope uses are present; the others are deleted
- [ ] No row repeats a row from the map above it in the chain, at any
      scope and in any section
- [ ] `generated_from` matches the current head of the mapped
      repositories; a spec or architecture change since then means the map
      is stale
- [ ] No entry spans more than one line, and no section contains prose
      paragraphs
- [ ] No entry names a skill, a persona, or a workflow stage
- [ ] No entry summarizes, quotes or restates the content of the file it
      points at
- [ ] Term and Context together are unique across the Glossary

**Judgment**

- [ ] Routing entries are written in the vocabulary a person would use
      when asking, not in the system's internal names
- [ ] Each glossary term carries its meaning AT THIS SCOPE; terms whose
      ordinary meaning already applies have been left out
- [ ] The Index's granularity matches what an agent would need to locate,
      neither one row per repository nor one row per file
- [ ] Each map refines the level above rather than restating it, and holds
      only what is true at its own scope
- [ ] An org map exists only where the organization runs several projects,
      and carries only vocabulary and routing every project shares
