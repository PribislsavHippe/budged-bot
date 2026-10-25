"""Очередь распознавания: фото ждут своего места, а не получают отказ «занято».

Лимит Groq общий на всех сотрудников. Одновременно читаются два фото, остальные
встают в очередь по порядку; сообщение о статусе правится на месте, новых не шлём.
Каждое задание получает общий бюджет времени, внутри которого темп-шлюз
(report_vision) ждёт места в минутном лимите для каждого своего запроса.
"""
import asyncio
from contextlib import asynccontextmanager

import report_vision as vision

SLOTS = 2
MAX_WAITING = 10       # дальше — «очередь длинная»
MAX_QUEUE_WAIT = 600   # сколько секунд фото может ждать начала чтения
JOB_BUDGET = 600       # сколько секунд на всё чтение после начала

QUEUED = 'Твоё фото в очереди — прочитаю, как только дойдёт очередь. Ничего делать не нужно.'
TOO_LONG = 'Сейчас очередь длинная. Пришли фото чуть позже, ничего не записал.'
STALLED = 'Очередь затянулась, поэтому фото не прочитал. Ничего не записал. Пришли его ещё раз чуть позже.'


class QueueFull(vision.VisionError):
    pass


class QueueStalled(vision.VisionError):
    pass


async def _say(status, text):
    # Статус — удобство: сбой правки сообщения не должен ломать чтение.
    if status is None:
        return
    try:
        await status.edit_text(text)
    except Exception:
        pass


class RecognitionQueue:
    def __init__(self):
        self._slots = asyncio.Semaphore(SLOTS)
        self.waiting = 0

    @asynccontextmanager
    async def turn(self, status=None, resume=None):
        """Место для чтения. `status` — сообщение, которое правим, пока фото ждёт;
        `resume` — прежний текст статуса, когда очередь дошла."""
        queued = False
        if self._slots.locked():
            if self.waiting >= MAX_WAITING:
                raise QueueFull(TOO_LONG)
            queued = True
            self.waiting += 1
            try:
                await _say(status, QUEUED)
                try:
                    async with asyncio.timeout(MAX_QUEUE_WAIT):
                        await self._slots.acquire()
                except TimeoutError:
                    raise QueueStalled(STALLED) from None
            finally:
                self.waiting -= 1
        else:
            await self._slots.acquire()
        try:
            if queued and resume:
                await _say(status, resume)
            token = vision.job_deadline.set(vision._clock() + JOB_BUDGET)
            try:
                yield
            finally:
                vision.job_deadline.reset(token)
        finally:
            self._slots.release()


queue = RecognitionQueue()
