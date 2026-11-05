from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fhir.api import create_app
from fhir.db import Base
from fhir.service import ConflictError, FhirService, NotFoundError, ValidationError

API_KEYS = {"admin-key": "admin", "integrator-key": "integrator", "viewer-key": "viewer"}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    yield db
    db.close()


@pytest.fixture()
def client(session):
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    return TestClient(create_app(session_factory=factory, api_keys=API_KEYS, init_database=False))


def patient(pid="P1", name="Aroha"):
    return {"resourceType": "Patient", "id": pid, "name": [{"family": name}]}


def encounter(eid="E1", pid="P1"):
    return {"resourceType": "Encounter", "id": eid, "status": "finished", "subject": {"reference": f"Patient/{pid}"}}


def observation(oid="O1", pid="P1", code="8867-4"):
    return {
        "resourceType": "Observation", "id": oid, "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "subject": {"reference": f"Patient/{pid}"}, "valueQuantity": {"value": 72, "unit": "bpm"},
    }


def bundle(*resources):
    return {"resourceType": "Bundle", "type": "transaction", "entry": [{"resource": r} for r in resources]}


def test_ingest_patient(session):
    service = FhirService(session)
    assert service.ingest_bundle(bundle(patient())) == {"created": 1, "unchanged": 0}


def test_duplicate_patient_unchanged(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient()))
    assert service.ingest_bundle(bundle(patient())) == {"created": 0, "unchanged": 1}


def test_conflicting_patient(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient()))
    with pytest.raises(ConflictError):
        service.ingest_bundle(bundle(patient(name="Different")))


def test_unsupported_resource(session):
    with pytest.raises(ValidationError):
        FhirService(session).ingest_bundle(bundle({"resourceType": "Device", "id": "D1"}))


def test_patient_requires_name(session):
    with pytest.raises(ValidationError):
        FhirService(session).ingest_bundle(bundle({"resourceType": "Patient", "id": "P1"}))


def test_encounter_requires_patient_reference(session):
    with pytest.raises(ValidationError):
        FhirService(session).ingest_bundle(bundle({"resourceType": "Encounter", "id": "E1", "status": "finished"}))


def test_ingest_full_bundle(session):
    service = FhirService(session)
    assert service.ingest_bundle(bundle(patient(), encounter(), observation())) == {"created": 3, "unchanged": 0}


def test_get_patient(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient()))
    assert service.get("Patient", "P1")["name"][0]["family"] == "Aroha"


def test_missing_resource(session):
    with pytest.raises(NotFoundError):
        FhirService(session).get("Patient", "NOPE")


def test_observation_requires_code(session):
    bad = observation()
    bad["code"] = {"coding": []}
    with pytest.raises(ValidationError):
        FhirService(session).ingest_bundle(bundle(patient(), bad))


def test_everything(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient(), encounter(), observation()))
    result = service.everything("P1")
    types = [e["resource"]["resourceType"] for e in result["entry"]]
    assert types == ["Patient", "Encounter", "Observation"]


def test_search_observations(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient(), observation(code="8867-4"), observation(oid="O2", code="1234-5")))
    result = service.search_observations("P1", "8867-4")
    assert result["total"] == 1


def test_transaction_rolls_back_on_conflict(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient()))
    with pytest.raises(ConflictError):
        service.ingest_bundle(bundle(patient(name="Different"), encounter()))
    with pytest.raises(NotFoundError):
        service.get("Encounter", "E1")


def test_summary(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient(), encounter(), observation()))
    summary = service.summary()
    assert summary == {"patients": 1, "encounters": 1, "observations": 1}


def test_audit(session):
    service = FhirService(session)
    service.ingest_bundle(bundle(patient()))
    actions = [e["action"] for e in service.audit()]
    assert "resource_created" in actions


def test_api_auth_and_workflow(client):
    assert client.post("/fhir/Bundle", json={}).status_code == 401
    headers = {"X-API-Key": "integrator-key"}
    payload = bundle(patient(), encounter(), observation())
    result = client.post("/fhir/Bundle", json=payload, headers=headers).json()
    assert result["created"] == 3
    patient_resource = client.get("/fhir/Patient/P1", headers={"X-API-Key": "viewer-key"}).json()
    assert patient_resource["resourceType"] == "Patient"
    everything = client.get("/fhir/Patient/P1/$everything", headers={"X-API-Key": "viewer-key"}).json()
    assert everything["total"] == 3
