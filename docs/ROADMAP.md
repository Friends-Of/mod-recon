# Roadmap

Mod Recon is an open-source tool for reliably monitoring Arma Reforger server mod manifests, detecting confirmed changes, preserving useful history, and reporting what changed.

These milestones are directional, not promises. Usage and technical discoveries may change them.

## v0.1 — Prototype / NA7 pilot — Complete

Proved reliable detection and Discord webhook reporting of real server manifest changes.

## v0.2 — Multi-server engine — Complete

Added configurable monitoring of arbitrary public Reforger servers with independent state, history, confirmation, Discord webhook destinations, and server lookup.

## v0.2.9 — Production hardening — Current

Validate the multi-server engine under sustained real-world operation and prepare the architecture for a stable event model.

Focus:

- continuous production testing
- API-budget management
- shared exact-version metadata caching
- optional authenticated upstream API support
- staggered multi-server polling
- restart and persistence validation
- reliability fixes discovered through operation

## v0.3 — Change Events

Make confirmed server changes first-class, structured events independent of how they are delivered.

A change event should reliably capture:

- server identity
- observation and confirmation timestamps
- added, removed, and updated mods
- exact old and new versions
- available package-size information
- reliable Workshop metadata
- meaningful grouping where supported by the data
- stable event IDs
- data provenance

Existing Discord webhook delivery should consume this same event model rather than contain change-detection or interpretation logic of its own.

## v0.4 — Integration and stability

Establish clean boundaries for other software to consume Mod Recon's monitoring and change-event capabilities.

Focus:

- programmatic ChangeEvent consumption
- separation of monitoring from delivery
- stable configuration and database migrations
- documented integration interfaces
- upstream failure handling
- security and secret handling
- installation and self-hosting documentation
- test coverage
- performance and reliability

## v0.5 — Core maturity

Resolve remaining prototype assumptions and validate Mod Recon under longer-term real-world use.

Focus on:

- reliability
- compatibility
- data quality
- maintainability
- improvements that naturally belong in the monitoring engine

Avoid expanding Mod Recon into:

- a general Reforger server browser
- a community-management platform
- a billing product
- a hosted dashboard
- a general analytics product

## v1.0 — Stable release

A dependable, documented, self-hostable release for monitoring Arma Reforger server mod changes.

Future releases should continue improving reliability, compatibility, upstream support, and the quality of change information.
