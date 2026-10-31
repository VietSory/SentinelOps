from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime,timezone
import json
import logging
import re
import secrets

import asyncpg
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sentinelops.api_http import ApiError, RequestSafetyMiddleware
from sentinelops.api_settings import Settings
from sentinelops.contracts import utc_time,validate_payload
from sentinelops.demo import demo_incident
from sentinelops.incidents.domain import IncidentConflict,IncidentNotFound,IncidentWrite
from sentinelops.incidents.repository import IncidentRepository,RepositoryIntegrityError
from sentinelops.telemetry.core import stable_id
from sentinelops.ml.registry import ModelRegistry, ModelUnavailable, IncompatibleFeatures
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.ui.routes import router as ui_router

logger = logging.getLogger("sentinelops-api")
STATES = {"degrading","incident","recovering","recovered","escalated"}
STORE_ERRORS = (asyncpg.PostgresError,asyncpg.InterfaceError,OSError,TimeoutError)


def expected_revision(request: Request,*,create: bool = False) -> int:
    value = request.headers.get("if-match")
    if value is None:
        raise ApiError(428,"expected_revision_required",'Supply If-Match: "0" to create or the quoted current revision to update')
    if not re.fullmatch(r'"[0-9]{1,19}"',value):
        raise ApiError(422,"invalid_expected_revision","If-Match must contain one quoted numeric revision")
    revision = int(value[1:-1])
    if revision>2**63-2 or (create and revision!=0) or (not create and revision<1):
        raise ApiError(422,"invalid_expected_revision","Expected revision is outside the operation bounds")
    return revision


def idempotency_key(request: Request) -> str:
    value = request.headers.get("idempotency-key","")
    if not value.strip() or value!=value.strip() or len(value)>256:
        raise ApiError(422,"idempotency_key_required","Supply a stable nonempty Idempotency-Key of at most 256 characters")
    return value


def validate_ier(payload: dict) -> None:
    try:
        validate_payload("incident-evidence-record.schema.json",payload)
        if utc_time(payload["detected_at"])>datetime.now(timezone.utc):
            raise ValueError("incident detection timestamp cannot be in the future")
    except ValueError as exc:
        raise ApiError(422,"invalid_payload",str(exc)[:500]) from exc


def encode_cursor(item: dict,filters: dict) -> str:
    value = {"created_at":item["created_at"],"incident_id":item["incident_id"],"filters":stable_id(filters)}
    return base64.urlsafe_b64encode(json.dumps(value,sort_keys=True).encode()).decode().rstrip("=")


def decode_cursor(value: str | None,filters: dict) -> tuple | None:
    if value is None:
        return None
    try:
        if len(value)>2048:
            raise ValueError("oversized cursor")
        item = json.loads(base64.b64decode(value+"="*(-len(value)%4),altchars=b"-_",validate=True))
        if set(item)!={"created_at","incident_id","filters"} or item["filters"]!=stable_id(filters):
            raise ValueError("cursor filters differ")
        if not isinstance(item["incident_id"],str) or not 1<=len(item["incident_id"])<=256:
            raise ValueError("invalid cursor ID")
        return utc_time(item["created_at"]),item["incident_id"]
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise ApiError(422,"invalid_cursor","Cursor is invalid or belongs to different filters") from exc


def revision_headers(response: Response,snapshot: dict) -> None:
    response.headers["ETag"] = f'"{snapshot["revision"]}"'
    response.headers["X-Incident-Revision"] = str(snapshot["revision"])


def create_app(settings: Settings | None = None,*,repository=None,model_registry=None) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = model_registry if model_registry is not None else ModelRegistry()
    if model_registry is None and settings.model_store:
        registry = ModelRegistry.load(ArtifactStore(settings.model_store), settings.model_release_ref)

    @asynccontextmanager
    async def lifespan(app):
        pool = None
        if repository is not None:
            app.state.incident_repository = repository
        elif settings.control_database_url:
            # Lazy connections allow liveness during a source outage. Each use
            # rechecks readiness, so a restored DB can recover without restart.
            try:
                pool = await asyncpg.create_pool(settings.control_database_url,min_size=0,max_size=10,
                                                command_timeout=5,timeout=5)
                app.state.incident_repository = IncidentRepository(pool)
            except (ValueError,*STORE_ERRORS):
                logger.error("control_store_configuration_invalid")
        try:
            yield
        finally:
            if pool is not None:
                await pool.close()

    app = FastAPI(title="SentinelOps API",version="0.2.1",lifespan=lifespan,
                  description="Incident persistence with revision headers. Real inference is unavailable until a model is activated; local demo mode is explicit.")
    app.state.incident_repository = repository
    app.add_middleware(RequestSafetyMiddleware)
    app.include_router(ui_router)

    @app.exception_handler(ApiError)
    async def api_error(request,exc):
        headers = {"WWW-Authenticate":"Bearer"} if exc.status==401 else None
        return JSONResponse({"code":exc.code,"message":exc.message,"request_id":request.state.request_id},status_code=exc.status,headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        return JSONResponse({"code":"invalid_request","message":"Request parameters/body are invalid","request_id":request.state.request_id},status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        return JSONResponse({"code":"http_error","message":"Resource or HTTP operation is unavailable",
                             "request_id":request.state.request_id},status_code=exc.status_code)

    async def authorize(request: Request) -> str:
        if settings.environment not in ("local","test"):
            raise ApiError(503,"authentication_adapter_unavailable","Production identity adapter is not configured")
        if not settings.api_token:
            raise ApiError(503,"authentication_unavailable","Incident API authentication is not configured")
        value = request.headers.get("authorization","")
        scheme,_,token = value.partition(" ")
        if scheme.lower()!="bearer" or not secrets.compare_digest(token.encode(),settings.api_token.encode()):
            raise ApiError(401,"unauthorized","A valid bearer token is required")
        return "local-trusted-producer"

    async def require_repository(request: Request):
        repo = request.app.state.incident_repository
        if repo is None:
            raise ApiError(503,"control_store_unavailable","Incident storage is not configured")
        try:
            await repo.check_ready()
        except (*STORE_ERRORS,RuntimeError):
            raise ApiError(503,"control_store_unavailable","Incident storage is unavailable or has invalid runtime privileges") from None
        return repo

    async def call(operation):
        try:
            return await operation
        except IncidentNotFound:
            raise ApiError(404,"incident_not_found","Incident/revision was not found") from None
        except IncidentConflict as exc:
            raise ApiError(409,"incident_conflict",str(exc)) from None
        except RepositoryIntegrityError:
            raise ApiError(503,"stored_record_invalid","Stored incident snapshot failed integrity checks") from None
        except ValueError as exc:
            raise ApiError(422,"invalid_payload",str(exc)[:500]) from None
        except STORE_ERRORS:
            raise ApiError(503,"control_store_unavailable","Incident storage is unavailable") from None

    @app.get("/health")
    async def health():
        return {"status":"ok","service":"sentinelops-api","version":"0.2.1"}

    @app.get("/ready")
    async def ready(request: Request):
        storage = False
        try:
            await require_repository(request)
            storage = True
        except ApiError:
            pass
        dependencies = {"incident_store": storage,
            "authentication": bool(settings.api_token) and settings.environment in ("local","test"), "model": registry.available}
        ready = all(dependencies.values())
        return JSONResponse({"status":"ready" if ready else "not_ready","dependencies":dependencies,
            "demo_mode":settings.demo_mode},status_code=200 if ready else 503)

    @app.get("/ready/incidents")
    async def incidents_ready(identity=Depends(authorize),repo=Depends(require_repository)):
        return {"status":"ready","component":"incident-store"}

    @app.post("/v1/detect")
    async def detect(feature_window: dict,response: Response,request: Request):
        try:
            validate_payload("feature-window.schema.json",feature_window)
        except ValueError as exc:
            raise ApiError(422,"invalid_payload",str(exc)[:500]) from exc
        if not settings.demo_mode:
            try:
                result = registry.infer(feature_window, source_profile=request.headers.get("x-sentinelops-source-profile"))
            except ModelUnavailable:
                raise ApiError(503,"model_unavailable","No validated model bundle is activated") from None
            except (IncompatibleFeatures, ValueError):
                raise ApiError(422,"incompatible_feature_set","Feature schema, source or availability is incompatible") from None
            response.headers["X-SentinelOps-Model-Version"] = result["model_version"]
            return result["detection"]
        response.headers["X-SentinelOps-Mode"] = "demo"
        result = {"schema_version":"1.0","service":feature_window["service"],"observed_at":feature_window["window_end"],
            "anomaly_score":.5,"is_anomalous":False,"model_name":"isolation-forest","model_version":"skeleton-v0"}
        validate_payload("detection-result.schema.json",result)
        return result

    @app.post("/v1/risk")
    async def risk(feature_window: dict,response: Response,request: Request):
        try:
            validate_payload("feature-window.schema.json", feature_window)
        except ValueError as exc:
            raise ApiError(422,"invalid_payload",str(exc)[:500]) from exc
        try:
            result = registry.infer(feature_window, source_profile=request.headers.get("x-sentinelops-source-profile"))
        except ModelUnavailable:
            raise ApiError(503,"model_unavailable","No validated model bundle is activated") from None
        except (IncompatibleFeatures, ValueError):
            raise ApiError(422,"incompatible_feature_set","Feature schema, source or availability is incompatible") from None
        if result["risk"] is None:
            raise ApiError(503,"risk_model_unavailable","No validated degradation-risk model is activated")
        response.headers["X-SentinelOps-Model-Version"] = result["model_version"]
        return result["risk"]

    @app.post("/v1/incidents/demo")
    async def demo(response: Response):
        if not settings.demo_mode:
            raise ApiError(404,"demo_disabled","Demo fixtures are disabled")
        response.headers["X-SentinelOps-Mode"] = "demo"
        return demo_incident()

    @app.post("/v1/incidents",status_code=201)
    async def create_incident(payload: dict,request: Request,response: Response,
                              identity=Depends(authorize),repo=Depends(require_repository)):
        validate_ier(payload)
        revision = expected_revision(request,create=True)
        digest = stable_id(payload)
        source_refs = sorted({item["source_ref"] for item in payload["evidence"] if item.get("source_ref")}) or ["submitted-ier://"+digest]
        # This is an imported/manual assertion, not computed SLO/model evidence.
        # Its emission time must be retry-stable, so use supplied detected_at.
        context = {"availability":"UNKNOWN","recovery_source":None,"source_refs":source_refs,
            "config_refs":["config://trusted-ier-import/v1"],"trigger_sources":[{"source":"manual","signal_id":digest,
                "emitted_at":payload["detected_at"],"source_ref":"submitted-ier://"+digest}]}
        stored = await call(repo.write(IncidentWrite(payload,"OPEN",revision,idempotency_key(request),identity,"trusted IER import",context)))
        revision_headers(response,stored)
        response.headers["Location"] = "/v1/incidents/"+payload["incident_id"]
        return stored["payload"]

    @app.post("/v1/incidents/{incident_id}/revisions")
    async def update_incident(incident_id: str,body: dict,request: Request,response: Response,
                              identity=Depends(authorize),repo=Depends(require_repository)):
        try:
            validate_payload("incident-write.schema.json",body)
        except ValueError as exc:
            raise ApiError(422,"invalid_payload",str(exc)[:500]) from exc
        validate_ier(body["payload"])
        if body["payload"]["incident_id"]!=incident_id:
            raise ApiError(422,"incident_id_mismatch","Path and payload incident IDs differ")
        # Future explanation/action/verification writers must supply validated
        # results through their own adapters; an import token cannot forge them.
        if body["lifecycle_stage"] not in ("OPEN","RCA_READY","ESCALATED"):
            raise ApiError(503,"lifecycle_writer_unavailable","Explanation/action/recovery writers are not integrated")
        stored = await call(repo.write(IncidentWrite(body["payload"],body["lifecycle_stage"],expected_revision(request),
                            idempotency_key(request),identity,body["reason"],body["context"])))
        revision_headers(response,stored)
        return stored["payload"]

    @app.get("/v1/incidents")
    async def list_incidents(limit: int = Query(50,ge=1,le=100),cursor: str | None = None,
                             state: str | None = None,service: str | None = Query(None,max_length=256),
                             identity=Depends(authorize),repo=Depends(require_repository)):
        if state is not None and state not in STATES:
            raise ApiError(422,"invalid_state","Unknown incident state filter")
        filters = {"state":state,"service":service}
        rows = await call(repo.list_current(limit=limit,after=decode_cursor(cursor,filters),state=state,service=service))
        items = [{"incident_id":row["incident_id"],"revision":row["revision"],
                  **{key:row["payload"][key] for key in ("state","severity","detected_at","affected_services")}}
                 for row in rows[:limit]]
        result = {"schema_version":"1.0","items":items,
                  "next_cursor":encode_cursor(rows[limit-1],filters) if len(rows)>limit else None}
        validate_payload("incident-list.schema.json",result)
        return result

    @app.get("/v1/incidents/{incident_id}")
    async def get_incident(incident_id: str,request: Request,response: Response,
                           revision: int | None = Query(None,ge=1,le=2**63-1),identity=Depends(authorize),repo=Depends(require_repository)):
        stored = await call(repo.get(incident_id,revision))
        revision_headers(response,stored)
        if request.headers.get("if-none-match")==response.headers["ETag"]:
            response.status_code = 304
            return response
        return stored["payload"]

    @app.get("/v1/incidents/{incident_id}/context")
    async def context(incident_id: str,response: Response,revision: int | None = Query(None,ge=1,le=2**63-1),
                      after_revision: int = Query(0,ge=0,le=2**63-1),limit: int = Query(50,ge=1,le=100),
                      identity=Depends(authorize),repo=Depends(require_repository)):
        stored = await call(repo.get(incident_id,revision))
        events = await call(repo.history(incident_id,after_revision=after_revision,limit=limit,max_revision=stored["revision"]))
        revision_headers(response,stored)
        next_after = events[-1]["revision"] if events and events[-1]["revision"]<stored["revision"] else None
        result = {**stored,"timeline":{"events":events,"next_after_revision":next_after}}
        validate_payload("incident-envelope.schema.json",result)
        return result

    return app
