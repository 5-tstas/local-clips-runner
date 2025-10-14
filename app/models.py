# app/models.py
from __future__ import annotations
from typing import List, Literal, Optional, Tuple, Union, Any
from pydantic import BaseModel, Field

# Базовый класс: пропускаем лишние поля (extra="allow"), чтобы JSON был гибким
class BaseCfg(BaseModel):
    class Config:
        extra = "allow"


class Output(BaseCfg):
    size: Tuple[int, int] = (1280, 720)
    fps: Optional[int] = 30
    theme: Optional[str] = None
    bgColor: Optional[str] = None
    textColor: Optional[str] = None
    fontFamily: Optional[str] = None
    safeArea: Optional[Tuple[int, int, int, int]] = None
    # опциональные поля для общего конфига (если их хочет прокинуть автор)
    cpsPrompt: Optional[int] = None
    cpsAnswer: Optional[int] = None
    pauseSentence: Optional[int] = None
    pauseComma: Optional[int] = None
    soundOn: Optional[bool] = None
    thinkSec: Optional[int] = None


# ПЛАТФОРМЫ
class OverlayPayload(BaseCfg):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    body: Optional[Union[str, List[str]]] = None
    vCenter: Optional[bool] = None


class ChatPayload(BaseCfg):
    prompt: Optional[str] = None
    answer: Optional[str] = None   # уже готовый markdown
    lines: Optional[List[str]] = None
    plain: Optional[str] = None    # исходный plain-текст (до конвертации в markdown)
    text: Optional[str] = None     # синоним plain
    # Скорости/паузы/прочее
    cpsPrompt: Optional[int] = None
    cpsAnswer: Optional[int] = None
    pauseSentence: Optional[int] = None
    pauseComma: Optional[int] = None
    fps: Optional[int] = None
    thinkSec: Optional[int] = None
    soundOn: Optional[bool] = None
    fontFamily: Optional[str] = None
    bgColor: Optional[str] = None
    textColor: Optional[str] = None


class ABCPayload(BaseCfg):
    images: List[str]
    captions: Optional[List[str]] = None
    perSlideSec: int = 6
    transition: Optional[str] = "fade"


Payload = Union[OverlayPayload, ChatPayload, ABCPayload]


class Job(BaseCfg):
    type: Literal["overlay", "chat", "abc"]
    name: str
    durationSec: Optional[int] = None
    payload: Payload


class Batch(BaseCfg):
    output: Output
    jobs: List[Job]


# ----------------- мягкая валидация и расстановка дефолтов -----------------

def _est_seconds_from_text(text: str, cps: float = 20.0,
                           pause_sent_ms: int = 220, pause_comma_ms: int = 110,
                           base_sec: float = 2.0, tail_sec: float = 1.2) -> int:
    """
    Грубая оценка длительности: длина/скорость + паузы + небольшой хвост.
    """
    import re
    text = text or ""
    sent = len(re.findall(r"[.!?…]", text))
    comm = len(re.findall(r"[,;:]", text))
    sec = base_sec + (len(text) / max(1.0, cps)) + (pause_sent_ms * sent + pause_comma_ms * comm) / 1000.0 + tail_sec
    return int(round(max(4.0, min(sec, 30.0))))  # ограничим 4–30с, чтобы не улетало совсем


def validate_batch(batch: Batch) -> Batch:
    """
    Мягко нормализует/дополняет batch:
    - Для overlay/chat: если durationSec не указан → оценим по тексту (без жёстких ошибок).
    - Для abc: если durationSec не указан или не кратен 3*perSlideSec → выставим perSlideSec*3.
    - captions, если не даны, подставим из имён файлов.
    Никаких ValueError на длительность — только автоисправления.
    """
    for j in batch.jobs:
        # Overlay
        if j.type == "overlay":
            p: OverlayPayload = j.payload  # type: ignore[assignment]
            body_text = ""
            if isinstance(p.body, list):
                body_text = "\n\n".join(str(x) for x in p.body)
            elif isinstance(p.body, str):
                body_text = p.body
            # если длительность не задана — прикинем
            if j.durationSec is None:
                j.durationSec = _est_seconds_from_text(
                    (p.title or "") + "\n" + (p.subtitle or "") + "\n" + body_text,
                    cps=22.0
                )

        # Chat
        elif j.type == "chat":
            p: ChatPayload = j.payload  # type: ignore[assignment]
            text_plain = p.plain or p.text
            if not text_plain and p.lines:
                text_plain = "\n\n".join(p.lines)
            text_md = p.answer or text_plain or ""
            cps_pr = float(p.cpsPrompt or batch.output.cpsPrompt or 14)
            cps_an = float(p.cpsAnswer or batch.output.cpsAnswer or 20)
            # оценим: вопрос + ответ
            if j.durationSec is None:
                j.durationSec = _est_seconds_from_text(
                    (p.prompt or "") + "\n" + text_md,
                    cps=(cps_pr + cps_an) / 2.0
                )

        # ABC
        else:
            p: ABCPayload = j.payload  # type: ignore[assignment]
            # captions заполним по умолчанию, если не даны
            if not p.captions or len(p.captions) != 3:
                caps = []
                for path in (p.images or [])[:3]:
                    name = str(path).split("/")[-1]
                    caps.append(name.rsplit(".", 1)[0])
                while len(caps) < 3:
                    caps.append("")
                p.captions = caps[:3]
            # длительность: по 6 сек на слайд (или по значению perSlideSec)
            want = int(p.perSlideSec) * 3
            if not j.durationSec or j.durationSec != want:
                j.durationSec = want

    return batch
