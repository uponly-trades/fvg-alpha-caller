from fastapi import FastAPI

app = FastAPI(title="trade_executor")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
