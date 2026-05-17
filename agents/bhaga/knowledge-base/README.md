# BHAGA Knowledge Base

This directory holds BHAGA's persistent knowledge: schemas, per-store configuration, calibrated selectors, and learnings from live runs.

## Directory layout

| Path | Purpose | Gittracked? |
|------|---------|-------------|
| `schema/` | JSON Schemas for tip records, hours records, allocation outputs, ADP paste block format | Yes |
| `store-profiles/` | Per-store config: Square location ID, ADP company code, earnings code for tips, pay period schedule, employee name ↔ ADP file # map | No (gitignored — contains business identifiers) |
| `selectors/` | Calibrated CSS/ARIA selectors for the ADP RUN Time > Timecards page. Each file: `last_verified` date + selectors used | Yes |
| `learnings/` | Per-portal navigation patterns captured during live (collaborative) sessions | Yes (sanitized) |
| `*.json` (top-level) | Active state — last run, cached session cookie pointer, per-period draft data | No (gitignored) |

## Conventions

- **Store profiles are gitignored** because they contain Square location IDs, ADP company codes, and employee mapping data that's business-sensitive
- **Selectors are checked in** so the next session (or a future Jarvis fork) inherits the calibration. Always update `last_verified` when re-calibrating
- **Schemas are checked in** as the contract every skill writes against
