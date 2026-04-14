from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from maxapi import Bot, Dispatcher
from maxapi.methods.types.getted_updates import process_update_webhook


def build_webhook_app(
    dispatcher: Dispatcher,
    bot: Bot,
    secret: str | None,
    path: str,
) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post(path)
    async def max_webhook(
        request: Request,
        x_max_bot_api_secret: str | None = Header(default=None),
    ) -> JSONResponse:
        if secret and x_max_bot_api_secret != secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid MAX webhook secret.",
            )

        event_json = await request.json()
        event_object = await process_update_webhook(event_json=event_json, bot=bot)
        await dispatcher.handle(event_object)
        return JSONResponse(content={"ok": True}, status_code=200)

    return app


async def serve_webhook(
    dispatcher: Dispatcher,
    bot: Bot,
    host: str,
    port: int,
    log_level: str,
    secret: str | None,
    path: str,
) -> None:
    dispatcher.webhook_app = build_webhook_app(
        dispatcher=dispatcher,
        bot=bot,
        secret=secret,
        path=path,
    )
    await dispatcher.init_serve(
        bot=bot,
        host=host,
        port=port,
        log_level=log_level.lower(),
    )
