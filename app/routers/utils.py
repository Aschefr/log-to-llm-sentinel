import asyncio
from fastapi import Request, HTTPException
from app import logger

async def cancel_on_disconnect(request: Request, coro):
    """
    Exécute une coroutine tout en surveillant la déconnexion du client.
    Si le client se déconnecte (ex: via AbortController du frontend),
    la coroutine est annulée (CancelledError).
    """
    task = asyncio.create_task(coro)
    
    async def watch_disconnect():
        while True:
            if await request.is_disconnected():
                task.cancel()
                return
            await asyncio.sleep(1)
            
    watcher = asyncio.create_task(watch_disconnect())
    try:
        res = await task
        return res
    except asyncio.CancelledError:
        logger.info("Server", "Requête annulée suite à la déconnexion du client (Abandon côté frontend).")
        raise HTTPException(status_code=499, detail="client_closed_request")
    finally:
        watcher.cancel()
