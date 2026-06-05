# app/orders/main.py
import os, time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mangum import Mangum

app = FastAPI(title="orders-svc")

class Order(BaseModel):
    user_email: str
    item: str
    qty: int

@app.on_event("startup")
def startup():
    try:
        from app.common.db import init_tables
        init_tables()
    except Exception:
        pass

@app.get("/healthz")
def healthz():
    return {"service":"orders","version":os.environ.get("VERSION","dev"),"ts":int(time.time())}

@app.post("/orders")
def create_order(o: Order):
    try:
        from app.common.db import execute, DBUnavailable
        res = execute("INSERT INTO orders (user_email, item, qty) VALUES (%s, %s, %s)", [o.user_email, o.item, o.qty])
        return {"ok": True, "inserted": res["rowcount"]}
    except DBUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/orders")
def list_orders():
    try:
        from app.common.db import select, DBUnavailable
        rows = select("SELECT id,user_email,item,qty,created_at FROM orders ORDER BY id DESC")
        return rows
    except DBUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

handler = Mangum(app)

