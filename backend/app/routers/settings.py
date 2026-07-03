import json
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = DATA_DIR / "app_settings.json"

DEFAULTS = {
    "output_protocol": "openai",
    "default_model": "",
    "default_provider_id": 0,
}

class AppSettings(BaseModel):
    output_protocol: str = "openai"
    default_model: str = ""
    default_provider_id: int = 0

def _get_settings() -> dict:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        default = DEFAULTS.copy()
        default.update(data)
        return default
    return DEFAULTS.copy()

def _save_settings(data: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_output_protocol() -> str:
    return _get_settings().get("output_protocol", "openai")

@router.get("")
async def get_settings():
    return _get_settings()

@router.put("")
async def update_settings(data: AppSettings):
    current = _get_settings()
    current.update(data.model_dump(exclude_unset=True))
    _save_settings(current)
    return current
