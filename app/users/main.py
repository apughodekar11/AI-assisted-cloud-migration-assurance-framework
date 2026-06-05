# app/users/main.py
import os, time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from mangum import Mangum

app = FastAPI(title="users-svc")

class User(BaseModel):
    email: EmailStr
    name: str

@app.on_event("startup")
def startup():
    # Try to init tables, but never fail app
    try:
        from app.common.db import init_tables
        init_tables()
    except Exception:
        pass

@app.get("/healthz")
def healthz():
    # Pure process health — no DB, no env
    return {"service":"users","version":os.environ.get("VERSION","dev"),"ts":int(time.time())}

@app.post("/users")
def create_user(u: User):
    try:
        from app.common.db import execute, DBUnavailable
        res = execute("INSERT INTO users (email, name) VALUES (%s, %s)", [u.email, u.name])
        return {"ok": True, "inserted": res["rowcount"]}
    except DBUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/users")
def list_users():
    try:
        from app.common.db import select, DBUnavailable
        rows = select("SELECT id,email,name,created_at FROM users ORDER BY id DESC")
        return rows
    except DBUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

handler = Mangum(app)

