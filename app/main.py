from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import os
import time

from .database import engine, Base
from .config import settings
from .routes import users, documents, sharing, comments
from .websocket import document_socket
from .websocket.connection_manager import manager


# ─────────────────────────────────────────────
# Lifespan: startup / shutdown
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables ready")
    yield
    # Shutdown
    print("👋 Shutting down...")


# ─────────────────────────────────────────────
# App instance
# ─────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description="Real-Time Collaborative Notes — College Project",
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Process-Time"] = f"{elapsed:.4f}s"
    return response


# ─────────────────────────────────────────────
# API Routers
# ─────────────────────────────────────────────

app.include_router(users.router)
app.include_router(documents.router)
app.include_router(sharing.router)
app.include_router(comments.router)
app.include_router(document_socket.router)

# ─────────────────────────────────────────────
# Frontend page routes  (must come BEFORE static mount)
# ─────────────────────────────────────────────

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")


def _page(filename: str):
    return FileResponse(os.path.join(FRONTEND, filename))


@app.get("/", include_in_schema=False)
def root():
    return _page("login.html")

@app.get("/login", include_in_schema=False)
def login_page():
    return _page("login.html")

@app.get("/register", include_in_schema=False)
def register_page():
    return _page("register.html")

@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return _page("dashboard.html")

@app.get("/editor", include_in_schema=False)
def editor_page():
    return _page("editor.html")

@app.get("/profile", include_in_schema=False)
def profile_page():
    return _page("profile.html")

@app.get("/shared/{token}", include_in_schema=False)
def shared_page(token: str):
    return _page("shared.html")

# ─────────────────────────────────────────────
# Health & Stats
# ─────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@app.get("/api/stats", tags=["System"])
def live_stats():
    """Live stats: active WebSocket connections, open document rooms."""
    return {
        "active_connections": manager.total_connections(),
        "open_document_rooms": len(manager.rooms),
        "rooms": {
            doc_id: room.user_count()
            for doc_id, room in manager.rooms.items()
        },
    }


# ─────────────────────────────────────────────
# Static files  (after routes)
# ─────────────────────────────────────────────

if os.path.exists(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


# ─────────────────────────────────────────────
# Entry point (dev)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
