from datetime import date, timedelta
import pytest
from fhir.provider_directory import (
    LicenseStatus, ProviderLicense, ProviderProfile,
    export_fhir_practitioner, export_fhir_practitioner_role,
    resolve_provider_entity, validate_npi
)

def test_npi_validation():
    assert validate_npi("1234567893") is True
    assert validate_npi("1234567890") is False
    assert validate_npi("12345") is False
    assert validate_npi("") is False
    assert validate_npi(None) is False  # type: ignore

def test_provider_license_validation():
    today = date.today()
    fut, past = today + timedelta(days=100), today - timedelta(days=10)
    assert ProviderLicense("CA", "C1", LicenseStatus.ACTIVE, fut).is_valid() is True
    assert ProviderLicense("NY", "N1", LicenseStatus.ACTIVE, past).is_valid() is False
    assert ProviderLicense("TX", "T1", LicenseStatus.SUSPENDED, fut).is_valid() is False

def test_entity_resolution():
    fut = date.today() + timedelta(days=100)
    p = ProviderProfile("1234567893", "Jane", "Doe", "207Q00000X",
                        [ProviderLicense("WA", "MD1", LicenseStatus.ACTIVE, fut)])
    d = [p]

    m1, s1, r1 = resolve_provider_entity({"npi": "1234567893"}, d)
    assert m1 is p and s1 == 1.0 and r1 == "EXACT_NPI_MATCH"

    m2, s2, r2 = resolve_provider_entity({"state": "WA", "license_number": "MD1"}, d)
    assert m2 is p and s2 == 0.95 and r2 == "LICENSE_JURISDICTION_MATCH"

    m3, s3, r3 = resolve_provider_entity({"first_name": "jane", "last_name": "doe", "taxonomy_code": "207Q00000X"}, d)
    assert m3 is p and s3 == 0.85 and r3 == "NAME_TAXONOMY_MATCH"

    m4, s4, r4 = resolve_provider_entity({"npi": "9999999999"}, d)
    assert m4 is None and s4 == 0.0 and r4 == "NO_MATCH"

def test_export_fhir():
    fut = date.today() + timedelta(days=100)
    p = ProviderProfile("1234567893", "Eleanor", "Vance", "207Q00000X",
                        [ProviderLicense("MA", "MA77", LicenseStatus.ACTIVE, fut)])
    pr = export_fhir_practitioner(p)
    assert pr["resourceType"] == "Practitioner" and pr["active"] is True
    assert pr["name"][0]["family"] == "Vance" and pr["identifier"][0]["value"] == "1234567893"

    role = export_fhir_practitioner_role(p, "org-1", "Family Medicine")
    assert role["resourceType"] == "PractitionerRole"
    assert role["practitioner"]["reference"] == "Practitioner/pr-1234567893"
