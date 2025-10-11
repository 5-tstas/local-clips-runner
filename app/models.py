# app/models.py
from typing import List, Literal, Optional, Tuple, Union
from pydantic import BaseModel

# ---------- Базовые настройки выхода (WEBM по умолчанию) ----------
class Output(BaseModel):
    size: Tuple[int, int] = (1280, 720)
    theme: Optional[Literal['dark', 'light', 'brand']] = 'dark'
    bgColor: str = '#000000'
    textColor: str = '#FFFFFF'
    fontFamily: str = 'Inter, Arial, sans-serif'
    safeArea: Tuple[int, int, int, int] = (48, 48, 48, 48)

# ---------- Payload’ы трёх типов клипов ----------
class OverlayPayload(BaseModel):
    title: str
    subtitle: Optional[str] = None
    body: List[str] = []
    vCenter: bool = True
    bgColor: Optional[str] = None
    textColor: Optional[str] = None
    fontFamily: Optional[str] = None

class ChatPayload(BaseModel):
    lines: List[str]
    typingSpeed: float = 1.0   # 0.5–2.0
    cursor: bool = True
    bgColor: Optional[str] = None
    textColor: Optional[str] = None
    fontFamily: Optional[str] = None

class ABCPayload(BaseModel):
    images: List[str]          # ровно 3
    captions: List[str]        # ровно 3
    perSlideSec: int = 3
    transition: Literal['fade', 'slide', 'none'] = 'fade'
    bgColor: Optional[str] = None
    textColor: Optional[str] = None
    fontFamily: Optional[str] = None

# ---------- Job / Batch ----------
JobType = Literal['overlay', 'chat', 'abc']

class Job(BaseModel):
    type: JobType
    name: str
    durationSec: int
    payload: Union[OverlayPayload, ChatPayload, ABCPayload]

class Batch(BaseModel):
    output: Output
    jobs: List[Job]

# ---------- Простая проверка правил ----------
def validate_batch(b: Batch) -> None:
    for j in b.jobs:
        if j.type == 'overlay':
            if j.durationSec > 20:
                raise ValueError(f"{j.name}: overlay.durationSec ≤ 20")
            p: OverlayPayload = j.payload  # type: ignore
            if len(p.title.split()) > 6:
                raise ValueError(f"{j.name}: title ≤ 6 слов")
            if p.subtitle and len(p.subtitle.split()) > 12:
                raise ValueError(f"{j.name}: subtitle ≤ 12 слов")
            for s in p.body:
                if len(s.split()) > 18:
                    raise ValueError(f"{j.name}: строки body ≤ 18 слов")

        elif j.type == 'chat':
            if j.durationSec > 30:
                raise ValueError(f"{j.name}: chat.durationSec ≤ 30")
            p: ChatPayload = j.payload  # type: ignore
            if not (2 <= len(p.lines) <= 6):
                raise ValueError(f"{j.name}: chat.lines 2–6 строк")
            for s in p.lines:
                if len(s.split()) > 14:
                    raise ValueError(f"{j.name}: каждая строка chat ≤ 14 слов")

        elif j.type == 'abc':
            if j.durationSec > 12:
                raise ValueError(f"{j.name}: abc.durationSec ≤ 12")
            p: ABCPayload = j.payload  # type: ignore
            if len(p.images) != 3 or len(p.captions) != 3:
                raise ValueError(f"{j.name}: abc требует 3 images и 3 captions")
            if p.perSlideSec * 3 != j.durationSec:
                raise ValueError(f"{j.name}: perSlideSec*3 должно равняться durationSec")
