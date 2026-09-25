from fastapi import APIRouter

from app.api import auth, chat, documents, files, projects, templates

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(files.router)
api_router.include_router(templates.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
