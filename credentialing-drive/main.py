from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Identity-provider settings remain separate from app settings during migration.
load_dotenv(".env.better-auth", override=False)
load_dotenv(".env.clerk", override=False)

from app.routes import router

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(
    "/assets",
    StaticFiles(directory="react-dashboard/dist/assets", check_dir=False),
    name="react-assets",
)


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse("react-dashboard/dist/favicon.svg")


app.include_router(router)
