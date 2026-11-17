"""Reading a posted form without blocking the server.

Routes that read their form themselves (its fields vary) take `form: FormData =
Depends(posted_form)` and are plain `def`s. FastAPI then reads the body on the event loop and runs
the route in a worker thread, so the route's database work never blocks the event loop. Waiting for
a pooled connection on the event loop would stop every request, including the ones holding the
connections it waits for.
"""

from fastapi import Request
from starlette.datastructures import FormData


async def posted_form(request: Request) -> FormData:
    return await request.form()
