from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class LicenseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    PROBATION = "PROBATION"


NPI_PATTERN = re.compile(r"^\d{10}$")


def validate_npi(npi: str) -> bool:
    """Validate a 10-digit National Provider Identifier (NPI) via CMS Luhn-80840 check."""
    if not npi or not isinstance(npi, str):
        return False
    npi = npi.strip()
    if not NPI_PATTERN.match(npi):
        return False

    # 80840 prefix contributes 24 to the Luhn weighted sum.
    total = 24
    for idx, char in enumerate(npi):
        digit = int(char)
        if idx % 2 == 0:
            doubled = digit * 2
            total += (doubled // 10) + (doubled % 10)
        else:
            total += digit
    return (total % 10) == 0


@dataclass
class ProviderLicense:
    state: str
    license_number: str
    status: LicenseStatus
    expiration_date: date
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_valid(self, as_of: Optional[date] = None) -> bool:
        check_date = as_of or date.today()
        return self.status == LicenseStatus.ACTIVE and self.expiration_date >= check_date


@dataclass
class ProviderProfile:
    npi: str
    first_name: str
    last_name: str
    taxonomy_code: str
    licenses: List[ProviderLicense] = field(default_factory=list)

    def active_licensed_states(self, as_of: Optional[date] = None) -> List[str]:
        return sorted([lic.state for lic in self.licenses if lic.is_valid(as_of)])


def resolve_provider_entity(
    incoming: Dict[str, Any],
    directory: List[ProviderProfile],
) -> Tuple[Optional[ProviderProfile], float, str]:
    """Resolve incoming provider record against directory with confidence weights."""
    incoming_npi = str(incoming.get("npi") or "").strip()
    if incoming_npi and validate_npi(incoming_npi):
        for record in directory:
            if record.npi == incoming_npi:
                return record, 1.0, "EXACT_NPI_MATCH"

    inc_lic = str(incoming.get("license_number") or "").strip().upper()
    inc_state = str(incoming.get("state") or "").strip().upper()
    if inc_lic and inc_state:
        for record in directory:
            for lic in record.licenses:
                if lic.state.upper() == inc_state and lic.license_number.upper() == inc_lic:
                    return record, 0.95, "LICENSE_JURISDICTION_MATCH"

    inc_first = str(incoming.get("first_name") or "").strip().lower()
    inc_last = str(incoming.get("last_name") or "").strip().lower()
    inc_tax = str(incoming.get("taxonomy_code") or "").strip().upper()
    if inc_first and inc_last and inc_tax:
        for record in directory:
            if (record.first_name.lower() == inc_first and record.last_name.lower() == inc_last
                    and record.taxonomy_code.upper() == inc_tax):
                return record, 0.85, "NAME_TAXONOMY_MATCH"

    return None, 0.0, "NO_MATCH"


def export_fhir_practitioner(provider: ProviderProfile) -> Dict[str, Any]:
    """Convert an internal provider profile into a standard FHIR R4 Practitioner resource."""
    identifiers: List[Dict[str, Any]] = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": provider.npi}]
    qualifications: List[Dict[str, Any]] = []
    for lic in provider.licenses:
        identifiers.append({"system": f"https://license.health.state.{lic.state.lower()}.gov", "value": lic.license_number})
        qualifications.append({
            "identifier": [{"value": lic.license_number}],
            "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0360", "code": "MD", "display": "Doctor of Medicine"}]},
            "period": {"end": lic.expiration_date.isoformat()},
            "issuer": {"display": f"{lic.state.upper()} State Medical Board"},
        })

    return {
        "resourceType": "Practitioner",
        "id": f"pr-{provider.npi}",
        "identifier": identifiers,
        "active": len(provider.active_licensed_states()) > 0,
        "name": [{"use": "official", "family": provider.last_name, "given": [provider.first_name]}],
        "qualification": qualifications,
    }


def export_fhir_practitioner_role(
    provider: ProviderProfile, organization_id: str, specialty_display: str = "General Practice"
) -> Dict[str, Any]:
    """Synthesize a FHIR R4 PractitionerRole connecting a provider to an organization."""
    return {
        "resourceType": "PractitionerRole",
        "id": f"role-{provider.npi}-{organization_id}",
        "active": True,
        "practitioner": {"reference": f"Practitioner/pr-{provider.npi}"},
        "organization": {"reference": f"Organization/{organization_id}"},
        "code": [{"coding": [{"system": "http://nucc.org/provider-taxonomy", "code": provider.taxonomy_code, "display": specialty_display}]}],
    }
