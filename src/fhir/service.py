from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .tables import AuditRow, ResourceRow, utcnow

SUPPORTED = {"Patient", "Encounter", "Observation"}


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


def canonical_hash(resource: dict) -> str:
    return hashlib.sha256(json.dumps(resource, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def patient_ref(resource: dict) -> str:
    reference = (resource.get("subject") or {}).get("reference", "")
    if not reference.startswith("Patient/"):
        raise ValidationError("resource subject must reference a Patient")
    return reference.split("/", 1)[1]


class FhirService:
    def __init__(self, session: Session, actor: str = "system") -> None:
        self.session = session
        self.actor = actor

    def _audit(self, action: str, reference: str, detail: str = "") -> None:
        self.session.add(AuditRow(action=action, actor=self.actor, reference=reference, detail=detail))

    def _validate(self, resource: dict) -> None:
        if not isinstance(resource, dict):
            raise ValidationError("resource must be an object")
        kind = resource.get("resourceType")
        if kind not in SUPPORTED:
            raise ValidationError(f"unsupported resourceType: {kind}")
        if not str(resource.get("id") or "").strip():
            raise ValidationError("resource id is required")
        if kind == "Patient":
            if not resource.get("name"):
                raise ValidationError("Patient name is required")
        elif kind == "Encounter":
            if not resource.get("status"):
                raise ValidationError("Encounter status is required")
            patient_ref(resource)
        elif kind == "Observation":
            if not resource.get("status"):
                raise ValidationError("Observation status is required")
            coding = ((resource.get("code") or {}).get("coding") or [])
            if not coding or not coding[0].get("code"):
                raise ValidationError("Observation code.coding[0].code is required")
            patient_ref(resource)

    def _fields(self, resource: dict) -> dict:
        kind = resource["resourceType"]
        patient_id = ""
        status = str(resource.get("status") or "")
        code = ""
        effective_at = ""
        if kind == "Patient":
            patient_id = resource["id"]
        elif kind == "Encounter":
            patient_id = patient_ref(resource)
            effective_at = str((resource.get("period") or {}).get("start") or "")
        elif kind == "Observation":
            patient_id = patient_ref(resource)
            code = str(((resource.get("code") or {}).get("coding") or [{}])[0].get("code") or "")
            effective_at = str(resource.get("effectiveDateTime") or "")
        return {"patient_id": patient_id, "status": status, "code": code, "effective_at": effective_at}

    def upsert(self, resource: dict) -> tuple[ResourceRow, str]:
        self._validate(resource)
        kind = resource["resourceType"]
        rid = str(resource["id"])
        digest = canonical_hash(resource)
        existing = self.session.scalar(select(ResourceRow).where(ResourceRow.resource_type == kind, ResourceRow.resource_id == rid))
        if existing is not None:
            if existing.content_hash == digest:
                return existing, "unchanged"
            raise ConflictError(f"{kind}/{rid} already exists with different content")
        fields = self._fields(resource)
        row = ResourceRow(resource_type=kind, resource_id=rid, payload=json.dumps(resource, sort_keys=True), content_hash=digest, **fields)
        self.session.add(row)
        self.session.flush()
        self._audit("resource_created", f"{kind}/{rid}")
        return row, "created"

    def ingest_bundle(self, bundle: dict) -> dict:
        if bundle.get("resourceType") != "Bundle":
            raise ValidationError("expected a FHIR Bundle")
        if bundle.get("type") not in {"transaction", "batch"}:
            raise ValidationError("Bundle.type must be transaction or batch")
        entries = bundle.get("entry") or []
        if not entries:
            raise ValidationError("Bundle.entry is required")
        created = unchanged = 0
        try:
            for entry in entries:
                resource = entry.get("resource") or {}
                _, decision = self.upsert(resource)
                if decision == "created":
                    created += 1
                else:
                    unchanged += 1
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return {"created": created, "unchanged": unchanged}

    def get(self, kind: str, rid: str) -> dict:
        if kind not in SUPPORTED:
            raise ValidationError("unsupported resourceType")
        row = self.session.scalar(select(ResourceRow).where(ResourceRow.resource_type == kind, ResourceRow.resource_id == rid))
        if row is None:
            raise NotFoundError(f"{kind}/{rid} not found")
        return json.loads(row.payload)

    def everything(self, patient_id: str) -> dict:
        patient = self.get("Patient", patient_id)
        rows = self.session.scalars(select(ResourceRow).where(ResourceRow.patient_id == patient_id).order_by(ResourceRow.id)).all()
        entries = [{"resource": patient}]
        for row in rows:
            if row.resource_type != "Patient":
                entries.append({"resource": json.loads(row.payload)})
        return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries}

    def search_observations(self, patient_id: str, code: str = "") -> dict:
        query = select(ResourceRow).where(ResourceRow.resource_type == "Observation", ResourceRow.patient_id == patient_id)
        if code:
            query = query.where(ResourceRow.code == code)
        rows = self.session.scalars(query.order_by(ResourceRow.id)).all()
        entries = [{"resource": json.loads(r.payload)} for r in rows]
        return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries}

    def summary(self) -> dict:
        rows = self.session.scalars(select(ResourceRow)).all()
        return {
            "patients": sum(1 for r in rows if r.resource_type == "Patient"),
            "encounters": sum(1 for r in rows if r.resource_type == "Encounter"),
            "observations": sum(1 for r in rows if r.resource_type == "Observation"),
        }

    def audit(self) -> list[dict]:
        rows = self.session.scalars(select(AuditRow).order_by(AuditRow.id)).all()
        return [{"at": r.at.isoformat(), "action": r.action, "actor": r.actor, "reference": r.reference, "detail": r.detail} for r in rows]
