"""Bounded request bodies and errors that never expose dependency credentials."""
import logging
from uuid import uuid4

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 1_000_000
logger = logging.getLogger("sentinelops-api")


class ApiError(Exception):
    def __init__(self,status,code,message):
        self.status,self.code,self.message = status,code,message


class RequestSafetyMiddleware:
    def __init__(self,app):
        self.app = app

    async def __call__(self,scope,receive,send):
        if scope["type"] != "http":
            return await self.app(scope,receive,send)
        request_id = uuid4().hex
        scope.setdefault("state",{})["request_id"] = request_id
        response_started = False
        async def send_with_identity(message):
            nonlocal response_started
            if message["type"]=="http.response.start":
                response_started = True
                message["headers"] = list(message["headers"])+[(b"x-request-id",request_id.encode()),(b"cache-control",b"no-store")]
            await send(message)
        async def reject(status,code,message):
            response = JSONResponse({"code":code,"message":message,"request_id":request_id},status_code=status)
            await response(scope,receive,send_with_identity)
        length = dict(scope.get("headers",[])).get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                return await reject(400,"invalid_content_length","Content length must be a nonnegative integer")
            if declared<0:
                return await reject(400,"invalid_content_length","Content length must be a nonnegative integer")
            if declared>MAX_BODY_BYTES:
                return await reject(413,"body_too_large","Request body exceeds 1MB")
        chunks,size = [],0
        while True:
            message = await receive()
            if message["type"]=="http.disconnect":
                return
            chunk = message.get("body",b"")
            size += len(chunk)
            if size>MAX_BODY_BYTES:
                return await reject(413,"body_too_large","Request body exceeds 1MB")
            chunks.append(chunk)
            if not message.get("more_body",False):
                break
        buffered = b"".join(chunks)
        delivered = False
        async def receive_buffered():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type":"http.request","body":buffered,"more_body":False}
            return await receive()
        try:
            await self.app(scope,receive_buffered,send_with_identity)
        except Exception as exc:
            logger.error("request_failed type=%s request_id=%s",type(exc).__name__,request_id)
            if response_started:
                raise RuntimeError("HTTP response transmission failed") from None
            await reject(500,"internal_error","Request could not be completed")
