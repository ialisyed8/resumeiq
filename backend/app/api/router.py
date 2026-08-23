"""Aggregate API router."""

from fastapi import APIRouter

from app.api.v1 import auth, candidates, jobs, reports, screenings, uploads

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(jobs.router)
api_router.include_router(uploads.router)
api_router.include_router(screenings.router)
api_router.include_router(candidates.router)
api_router.include_router(reports.router)
