---
artifact_type: context map
id: CTXMAP-quadra
scope: project
status: Generated
generated_from: none
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
|  |  |  |  |

## Glossary

| Term | Context | Means | Owned by |
|------|---------|-------|----------|
|  |  |  |  |

## Routing Table

| If the work concerns | Go to | Kind |
|----------------------|-------|------|

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
