from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fhir.api import create_app
from fhir.db import Base
from fhir.primary_care import (
    BatchFileControl,
    ClinicalExceptionAnalyzer,
    ConsultType,
    PrimaryCareConsultation,
    Severity,
    validate_nhi,
)

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


def test_nhi_validation_success():
    assert validate_nhi("ABC1235") is True
    assert validate_nhi("WGT4568") is True
    assert validate_nhi("abc1235") is True  # case insensitive


def test_nhi_validation_failures():
    assert validate_nhi("") is False
    assert validate_nhi(None) is False
    assert validate_nhi("ABI1234") is False  # 'I' not allowed
    assert validate_nhi("ABO1234") is False  # 'O' not allowed
    assert validate_nhi("ABC1239") is False  # Bad checksum
    assert validate_nhi("TOOLONG123") is False


def test_normal_consultation_no_exceptions():
    consult = PrimaryCareConsultation(
        consult_id="C001",
        nhi="ABC1235",
        patient_name="Hemi Taylor",
        consult_date="2026-11-14",
        consult_type=ConsultType.ROUTINE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
        systolic_bp=120,
        diastolic_bp=78,
        heart_rate_bpm=68,
        hba1c_mmol_mol=48.0,
        egfr_ml_min=85.0,
        snomed_codes=["422034002"],  # Routine check
    )
    analyzer = ClinicalExceptionAnalyzer()
    exceptions = analyzer.analyze_consultation(consult)
    assert len(exceptions) == 0


def test_hypertensive_crisis_exception():
    consult = PrimaryCareConsultation(
        consult_id="C002",
        nhi="ABC1235",
        patient_name="Mere Smith",
        consult_date="2026-11-14",
        consult_type=ConsultType.ACUTE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
        systolic_bp=195,
        diastolic_bp=125,
        snomed_codes=["38341003"],  # Hypertension
    )
    analyzer = ClinicalExceptionAnalyzer()
    exceptions = analyzer.analyze_consultation(consult)
    assert any(e.code == "EXC_BP_HYPERTENSIVE_CRISIS" and e.severity == Severity.CRITICAL for e in exceptions)


def test_uncontrolled_diabetes_hba1c_exception():
    consult = PrimaryCareConsultation(
        consult_id="C003",
        nhi="WGT4568",
        patient_name="Nikau Brown",
        consult_date="2026-11-14",
        consult_type=ConsultType.CHRONIC_CARE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
        hba1c_mmol_mol=78.5,
    )
    analyzer = ClinicalExceptionAnalyzer()
    exceptions = analyzer.analyze_consultation(consult)
    assert any(e.code == "EXC_HBA1C_UNCONTROLLED_DIABETES" and e.severity == Severity.WARNING for e in exceptions)


def test_severe_renal_impairment_egfr_exception():
    consult = PrimaryCareConsultation(
        consult_id="C004",
        nhi="WGT4568",
        patient_name="Wiremu Davis",
        consult_date="2026-11-14",
        consult_type=ConsultType.CHRONIC_CARE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
        egfr_ml_min=22.0,
    )
    analyzer = ClinicalExceptionAnalyzer()
    exceptions = analyzer.analyze_consultation(consult)
    assert any(e.code == "EXC_RENAL_SEVERE_IMPAIRMENT" and e.severity == Severity.CRITICAL for e in exceptions)


def test_acute_consult_missing_diagnosis_coding():
    consult = PrimaryCareConsultation(
        consult_id="C005",
        nhi="ABC1235",
        patient_name="Tane Wilson",
        consult_date="2026-11-14",
        consult_type=ConsultType.ACUTE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
        systolic_bp=128,
        diastolic_bp=82,
        # snomed_codes and read_codes both empty
    )
    analyzer = ClinicalExceptionAnalyzer()
    exceptions = analyzer.analyze_consultation(consult)
    assert any(e.code == "EXC_MISSING_DIAGNOSIS_CODING" and e.severity == Severity.WARNING for e in exceptions)


def test_duplicate_same_day_consult_detection():
    c1 = PrimaryCareConsultation(
        consult_id="C006A",
        nhi="ABC1235",
        patient_name="Kiri Jones",
        consult_date="2026-11-14",
        consult_type=ConsultType.ROUTINE,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
    )
    c2 = PrimaryCareConsultation(
        consult_id="C006B",
        nhi="ABC1235",
        patient_name="Kiri Jones",
        consult_date="2026-11-14",
        consult_type=ConsultType.TELEHEALTH,
        clinic_hpi_o="G99123-B",
        provider_hpi_cpn="CPN88124",
    )
    analyzer = ClinicalExceptionAnalyzer()
    batch_res = analyzer.analyze_batch([c1, c2])
    assert any(e["code"] == "EXC_DUPLICATE_SAME_DAY_CONSULT" for e in batch_res["exceptions"])


def test_batch_file_control_validation():
    header = {"batch_id": "B-20261114-001", "clinic_hpi_o": "G99123-B", "declared_records": 2}
    records = [{"consult_id": "C1"}, {"consult_id": "C2"}]
    accepted = BatchFileControl.validate_file_control(header, records)
    assert accepted["is_balanced"] is True
    assert accepted["status"] == "ACCEPTED"

    mismatch = BatchFileControl.validate_file_control(header, records[:1])
    assert mismatch["is_balanced"] is False
    assert mismatch["status"] == "REJECTED_COUNT_MISMATCH"


def test_api_primary_care_endpoints(client):
    # Test NHI lookup
    res_valid = client.get("/fhir/primary-care/nhi/ABC1235", headers={"X-API-Key": "viewer-key"}).json()
    assert res_valid["valid"] is True
    assert res_valid["nhi"] == "ABC1235"

    res_invalid = client.get("/fhir/primary-care/nhi/INVALID99", headers={"X-API-Key": "viewer-key"}).json()
    assert res_invalid["valid"] is False

    # Test batch validation endpoint
    batch_payload = {
        "header": {
            "batch_id": "NZ-PHO-BATCH-20261114",
            "clinic_hpi_o": "G00921-A",
            "declared_records": 1,
        },
        "consultations": [
            {
                "consult_id": "NZ-CONS-991",
                "nhi": "ABC1235",
                "patient_name": "Rawiri Walker",
                "consult_date": "2026-11-14",
                "consult_type": "ACUTE",
                "clinic_hpi_o": "G00921-A",
                "provider_hpi_cpn": "CPN11099",
                "systolic_bp": 188,
                "diastolic_bp": 115,
                "snomed_codes": ["38341003"],
            }
        ],
    }

    resp = client.post(
        "/fhir/primary-care/validate-batch",
        json=batch_payload,
        headers={"X-API-Key": "integrator-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_control"]["is_balanced"] is True
    assert body["analysis"]["total_consultations"] == 1
    assert body["analysis"]["breakdown"]["critical"] == 1
