from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from .auth import load_api_keys, require_role
from .db import SessionLocal, init_db
from .service import ConflictError, FhirService, NotFoundError, ValidationError


class BundleIn(BaseModel):
    resourceType: str
    type: str
    entry: list[dict[str, Any]]


def create_app(session_factory: sessionmaker = SessionLocal, api_keys: dict[str, str] | None = None, init_database: bool = True) -> FastAPI:
    if init_database:
        init_db()
    keys = api_keys or load_api_keys()
    app = FastAPI(title="FHIR interoperability service", version="1.0.0")

    def db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    viewer = require_role("viewer", keys)
    integrator = require_role("integrator", keys)

    def handle(exc: Exception):
        if isinstance(exc, NotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, ConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/fhir/Bundle")
    def bundle(payload: BundleIn, session: Session = Depends(db_session), actor: str = Depends(integrator)) -> dict[str, Any]:
        try:
            return FhirService(session, actor).ingest_bundle(payload.model_dump())
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/fhir/{kind}/{resource_id}")
    def get_resource(kind: str, resource_id: str, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return FhirService(session).get(kind, resource_id)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/fhir/Patient/{patient_id}/$everything")
    def everything(patient_id: str, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return FhirService(session).everything(patient_id)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/fhir/Observation")
    def search(patient: str, code: str = "", session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return FhirService(session).search_observations(patient, code)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/summary")
    def summary(session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return FhirService(session).summary()

    @app.get("/audit")
    def audit(session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return {"audit": FhirService(session).audit()}

    return app


app = create_app()
