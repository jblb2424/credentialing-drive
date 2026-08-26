from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "credentialing-drive-test",
    }


@app.post("/webhooks/google-drive")
async def google_drive_webhook(request: Request):
    print("Google Drive webhook received")
    return {
        "received": True,
    }
