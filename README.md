# FHIR Interoperability Service

Production-oriented FHIR R4 interoperability backend: transaction bundles,
Patient, Encounter and Observation resources, idempotent ingestion, conflict
detection, patient `$everything`, observation search and audit.

## Why this exists

Health interoperability fails at the seams: a bundle is retried, one resource
conflicts halfway through, references point at the wrong patient, or the same
message is ingested twice. This service models a small but real FHIR ingestion
surface with transactional behaviour and clear conflict handling.

## Features

- FHIR R4 Bundle ingestion (`transaction` or `batch`)
- Patient, Encounter and Observation resources
- Required-field validation for the supported resources
- Stable canonical hashing for idempotent retries
- Conflict detection when the same resource ID arrives with different content
- Transaction rollback when any entry conflicts
- Patient `$everything` bundle
- Observation search by patient and code
- Resource counts and append-only audit
- API-key RBAC: viewer, integrator, admin
- PostgreSQL or SQLite, SQLAlchemy 2, Alembic, Docker, GitHub Actions

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
DATABASE_URL=sqlite:///./fhir.db .venv/bin/alembic upgrade head
API_KEYS='{"dev-admin":"admin","dev-integrator":"integrator","dev-viewer":"viewer"}' \
  .venv/bin/uvicorn fhir.api:app --reload
```

## Test

```bash
.venv/bin/pytest
```

## Honest scope

This is a real, tested FHIR ingestion service, not a complete FHIR server or
health information exchange. It does not implement the full R4 resource model,
terminology validation, SMART-on-FHIR, subscriptions, consent, audit
ATNA/IHE profiles or HL7 v2 mapping. The README does not claim production usage.
