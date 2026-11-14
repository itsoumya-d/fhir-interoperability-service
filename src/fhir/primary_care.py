from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class ConsultType(str, Enum):
    ROUTINE = "ROUTINE"
    ACUTE = "ACUTE"
    CHRONIC_CARE = "CHRONIC_CARE"
    TELEHEALTH = "TELEHEALTH"
    IMMUNISATION = "IMMUNISATION"


# New Zealand NHI format: 3 letters (excluding I and O), followed by 4 digits.
# Valid letters: A-H, J-N, P-Z.
NHI_PATTERN = re.compile(r"^[A-HJ-NP-Z]{3}[0-9]{4}$")


def validate_nhi(nhi: str) -> bool:
    """Validate a New Zealand National Health Index (NHI) number format and checksum.

    Standard NZ NHI is 3 uppercase characters (excluding I, O) followed by 4 digits.
    The 7th character is a modulo-11 check digit.
    """
    if not nhi or not isinstance(nhi, str):
        return False
    nhi = nhi.strip().upper()
    if not NHI_PATTERN.match(nhi):
        return False

    # Alphabet conversion excluding I and O:
    # A=1, B=2 ... H=8, J=9, K=10, L=11, M=12, N=13, P=14 ... Z=24
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    char_map = {c: i + 1 for i, c in enumerate(alphabet)}

    multipliers = [7, 6, 5, 4, 3, 2]
    total = 0
    for idx in range(3):
        total += char_map[nhi[idx]] * multipliers[idx]
    for idx in range(3, 6):
        total += int(nhi[idx]) * multipliers[idx]

    remainder = total % 11
    check_digit = (11 - remainder) % 11

    # In standard NHI, if check_digit == 10, the NHI is invalid
    if check_digit == 10:
        return False
    return check_digit == int(nhi[6])


@dataclass
class ClinicalException:
    code: str
    severity: Severity
    message: str
    patient_nhi: str
    consult_id: Optional[str] = None
    recommended_action: Optional[str] = None


@dataclass
class PrimaryCareConsultation:
    consult_id: str
    nhi: str
    patient_name: str
    consult_date: str
    consult_type: ConsultType
    clinic_hpi_o: str
    provider_hpi_cpn: str
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    heart_rate_bpm: Optional[int] = None
    hba1c_mmol_mol: Optional[float] = None
    egfr_ml_min: Optional[float] = None
    snomed_codes: List[str] = field(default_factory=list)
    read_codes: List[str] = field(default_factory=list)
    medications: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PrimaryCareConsultation:
        ctype_str = str(data.get("consult_type", "ROUTINE")).upper()
        try:
            ctype = ConsultType(ctype_str)
        except ValueError:
            ctype = ConsultType.ROUTINE

        return cls(
            consult_id=str(data.get("consult_id", "")),
            nhi=str(data.get("nhi", "")).strip().upper(),
            patient_name=str(data.get("patient_name", "")),
            consult_date=str(data.get("consult_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))),
            consult_type=ctype,
            clinic_hpi_o=str(data.get("clinic_hpi_o", "")),
            provider_hpi_cpn=str(data.get("provider_hpi_cpn", "")),
            systolic_bp=int(data["systolic_bp"]) if data.get("systolic_bp") is not None else None,
            diastolic_bp=int(data["diastolic_bp"]) if data.get("diastolic_bp") is not None else None,
            heart_rate_bpm=int(data["heart_rate_bpm"]) if data.get("heart_rate_bpm") is not None else None,
            hba1c_mmol_mol=float(data["hba1c_mmol_mol"]) if data.get("hba1c_mmol_mol") is not None else None,
            egfr_ml_min=float(data["egfr_ml_min"]) if data.get("egfr_ml_min") is not None else None,
            snomed_codes=list(data.get("snomed_codes", [])),
            read_codes=list(data.get("read_codes", [])),
            medications=list(data.get("medications", [])),
        )


class ClinicalExceptionAnalyzer:
    """Evaluates primary care clinical consultation records against New Zealand

    Ministry of Health / Health New Zealand primary care standards.
    """

    def __init__(self) -> None:
        self.exceptions: List[ClinicalException] = []

    def analyze_consultation(self, consult: PrimaryCareConsultation) -> List[ClinicalException]:
        findings: List[ClinicalException] = []

        # 1. NHI Validation
        if not consult.nhi:
            findings.append(ClinicalException(
                code="EXC_NHI_MISSING",
                severity=Severity.CRITICAL,
                message="Consultation record missing mandatory National Health Index (NHI) identifier",
                patient_nhi=consult.nhi,
                consult_id=consult.consult_id,
                recommended_action="Reject record or flag for identity matching before patient record ingestion."
            ))
        elif not validate_nhi(consult.nhi):
            findings.append(ClinicalException(
                code="EXC_NHI_INVALID",
                severity=Severity.CRITICAL,
                message=f"NHI identifier '{consult.nhi}' failed standard checksum verification",
                patient_nhi=consult.nhi,
                consult_id=consult.consult_id,
                recommended_action="Verify patient NHI via Health New Zealand National Enrollment Service (NES)."
            ))

        # 2. Vital Signs Thresholds & Clinical Safety Checks
        if consult.systolic_bp is not None and consult.diastolic_bp is not None:
            if consult.systolic_bp >= 180 or consult.diastolic_bp >= 120:
                findings.append(ClinicalException(
                    code="EXC_BP_HYPERTENSIVE_CRISIS",
                    severity=Severity.CRITICAL,
                    message=f"Blood pressure {consult.systolic_bp}/{consult.diastolic_bp} mmHg indicates hypertensive crisis",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Immediate clinical escalation required; verify emergency protocol followed."
                ))
            elif consult.systolic_bp >= 140 or consult.diastolic_bp >= 90:
                findings.append(ClinicalException(
                    code="EXC_BP_STAGE2_HYPERTENSION",
                    severity=Severity.WARNING,
                    message=f"Blood pressure {consult.systolic_bp}/{consult.diastolic_bp} mmHg meets Stage 2 Hypertension criteria",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Ensure cardiovascular risk assessment (CVDRA) scheduled within 30 days."
                ))

        if consult.hba1c_mmol_mol is not None:
            if consult.hba1c_mmol_mol >= 65.0:
                findings.append(ClinicalException(
                    code="EXC_HBA1C_UNCONTROLLED_DIABETES",
                    severity=Severity.WARNING,
                    message=f"HbA1c level {consult.hba1c_mmol_mol} mmol/mol indicates poorly controlled diabetes (target < 53)",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Flag for diabetic annual review and structured medication review."
                ))

        if consult.egfr_ml_min is not None:
            if consult.egfr_ml_min < 30.0:
                findings.append(ClinicalException(
                    code="EXC_RENAL_SEVERE_IMPAIRMENT",
                    severity=Severity.CRITICAL,
                    message=f"eGFR {consult.egfr_ml_min} mL/min/1.73m2 indicates severe renal impairment (CKD Stage 4/5)",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Nephrology referral or urgent medication dose adjustment required."
                ))

        # 3. Clinical Coding Completeness (NZ Primary Care PHO contract requirement)
        if consult.consult_type == ConsultType.ACUTE:
            if not consult.snomed_codes and not consult.read_codes:
                findings.append(ClinicalException(
                    code="EXC_MISSING_DIAGNOSIS_CODING",
                    severity=Severity.WARNING,
                    message="Acute consultation submitted without SNOMED CT or NZ Read diagnosis coding",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Contact GP clinic practice manager to supply SNOMED clinical diagnostic term."
                ))

        # 4. Immunisation Event Tracking
        if consult.consult_type == ConsultType.IMMUNISATION:
            if not consult.medications and not consult.snomed_codes:
                findings.append(ClinicalException(
                    code="EXC_IMMUNISATION_PAYLOAD_EMPTY",
                    severity=Severity.WARNING,
                    message="Immunisation consultation lacks vaccine medication code or SNOMED procedure entry",
                    patient_nhi=consult.nhi,
                    consult_id=consult.consult_id,
                    recommended_action="Awhina National Immunisation Register (AIR) upload blocked until vaccine identified."
                ))

        return findings

    def analyze_batch(self, consultations: List[PrimaryCareConsultation]) -> Dict[str, Any]:
        all_exceptions: List[ClinicalException] = []
        consult_seen: Dict[str, str] = {}

        for c in consultations:
            # Duplicate consult detection
            key = f"{c.nhi}:{c.clinic_hpi_o}:{c.consult_date}"
            if key in consult_seen:
                all_exceptions.append(ClinicalException(
                    code="EXC_DUPLICATE_SAME_DAY_CONSULT",
                    severity=Severity.INFO,
                    message=f"Multiple consultations registered for patient on same date {c.consult_date} at clinic {c.clinic_hpi_o}",
                    patient_nhi=c.nhi,
                    consult_id=c.consult_id,
                    recommended_action="Review for claim deduplication before PHO capitation reporting."
                ))
            else:
                consult_seen[key] = c.consult_id

            findings = self.analyze_consultation(c)
            all_exceptions.extend(findings)

        self.exceptions = all_exceptions

        critical_count = sum(1 for e in all_exceptions if e.severity == Severity.CRITICAL)
        warning_count = sum(1 for e in all_exceptions if e.severity == Severity.WARNING)
        info_count = sum(1 for e in all_exceptions if e.severity == Severity.INFO)

        return {
            "total_consultations": len(consultations),
            "total_exceptions": len(all_exceptions),
            "breakdown": {
                "critical": critical_count,
                "warning": warning_count,
                "info": info_count,
            },
            "pho_quality_index": max(0.0, round(100.0 - (critical_count * 15.0 + warning_count * 4.0), 2)),
            "exceptions": [
                {
                    "code": e.code,
                    "severity": e.severity.value,
                    "message": e.message,
                    "patient_nhi": e.patient_nhi,
                    "consult_id": e.consult_id,
                    "recommended_action": e.recommended_action,
                }
                for e in all_exceptions
            ],
        }


class BatchFileControl:
    """Manages primary care file control headers, record balance, and audit trail."""

    @staticmethod
    def validate_file_control(header: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
        declared_count = header.get("declared_records", 0)
        actual_count = len(records)
        batch_id = header.get("batch_id", "UNKNOWN")
        clinic_hpi_o = header.get("clinic_hpi_o", "UNKNOWN")

        balanced = declared_count == actual_count
        return {
            "batch_id": batch_id,
            "clinic_hpi_o": clinic_hpi_o,
            "declared_records": declared_count,
            "actual_records": actual_count,
            "is_balanced": balanced,
            "status": "ACCEPTED" if balanced else "REJECTED_COUNT_MISMATCH",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
