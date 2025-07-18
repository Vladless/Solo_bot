from fastapi import FastAPI
from api.routes import users, keys, coupons, servers, tariffs, gifts, referrals, misc

app = FastAPI(
    title="SoloBot API (preAlpha)",
    version="0.2.0",
    docs_url="/api/docs",           
    redoc_url="/api/redoc",         
    openapi_url="/api/openapi.json" 
)


app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(keys.router, prefix="/api/keys", tags=["Keys"])
app.include_router(coupons.router, prefix="/api/coupons", tags=["Coupons"])
app.include_router(servers.router, prefix="/api/servers", tags=["Servers"])
app.include_router(tariffs.router, prefix="/api/tariffs", tags=["Tariffs"])
app.include_router(gifts.router, prefix="/api/gifts", tags=["Gifts"])
app.include_router(referrals.router, prefix="/api/referrals", tags=["Referrals"])
app.include_router(misc.router, prefix="/api")


@app.get("/api", include_in_schema=False)
async def root():
    return {"message": "Welcome to SoloBot API. Docs: /api/docs"}
