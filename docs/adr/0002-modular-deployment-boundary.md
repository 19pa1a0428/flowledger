# ADR 0002: Keep a modular deployment and defer paid cloud hosting

Status: Accepted

## Context

FlowLedger needs a credible deployment story for a professional portfolio. Its API, outbox relay
and worker have different process lifecycles, but share one domain model, schema and release. The
project already has reproducible Compose startup, container scanning, provenance/SBOM evidence,
query-plan checks and a destructive PostgreSQL restore drill. Hosting would add public networking,
secret management, provider configuration, recurring cost and operational ownership.

## Decision

Build and version one non-root application image, then run migrations, API, relay and worker as
separate commands from that image. Keep PostgreSQL authoritative and Redis as retained delivery
infrastructure. Docker Compose is the reference deployment and CI evidence boundary.

Do not provision optional cloud hosting now. A public endpoint would add little evidence beyond the
tested repository while creating cost and a security/maintenance surface. Reconsider hosting only
when there is a concrete need for a remote live demo, an explicit budget and owner, defined service
objectives, managed secrets/TLS, monitoring and a tested backup policy.

If those conditions arrive, prefer one container platform with the same process commands plus
managed PostgreSQL and Redis. Run Alembic as a one-shot release job and preserve backward-compatible
migrations. A provider selection belongs in a later ADR based on current pricing and requirements;
this decision does not imply a specific cloud.

## Consequences

- Local and CI behavior remains reproducible without paid resources or checked-in credentials.
- Process failures can be supervised independently while code stays in a modular application.
- The project demonstrates deployment inputs and recovery evidence, not public availability.
- Compose is not multi-host orchestration, automatic failover or a production security boundary.
- Redis loss after an outbox row is marked delivered still needs an operator-controlled
  reconciliation path; hosting would not remove that application-level recovery gap.

## Alternatives considered

- **Separate microservices:** rejected because independent ownership, scaling and release needs have
  not been demonstrated; the split would add distributed failure modes without portfolio value.
- **Immediate free-tier deployment:** rejected because free tiers and limits change, public exposure
  requires ongoing security work, and availability alone would not validate recovery semantics.
- **Serverless rewrite:** rejected because it would change runtime and worker assumptions before a
  measured need, obscuring rather than demonstrating the existing reliability design.
