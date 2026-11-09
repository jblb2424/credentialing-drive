"""Dedicated document worker entry point.

This app intentionally exposes no dashboard, OAuth, or Drive webhook routes.
Cloud Tasks is the only supported caller for document processing.
"""

from dotenv import load_dotenv
from fastapi import FastAPI, Request

# Keep worker configuration consistent with the dashboard process in local runs.
load_dotenv(".env.better-auth", override=False)
load_dotenv(".env.clerk", override=False)

from app.task_processor import process_drive_file_task


app = FastAPI()


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.post("/tasks/process-drive-file", status_code=204)
async def process_drive_file(request: Request):
    return await process_drive_file_task(request)
