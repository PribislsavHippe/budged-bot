"""Очередь распознавания: порядок, длинная очередь, затянувшаяся очередь, чистота слотов."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recognition_queue as rq
import report_vision as vision


class Status:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, queue, name, gate, order, status=None, resume='Читаю…'):
        async def job():
            async with queue.turn(status, resume):
                order.append(('start', name))
                await gate.wait()
                order.append(('end', name))
        task = asyncio.create_task(job())
        for _ in range(5):
            await asyncio.sleep(0)
        return task

    async def test_third_photo_waits_for_a_free_slot_and_is_told_so(self):
        queue = rq.RecognitionQueue()
        gates = [asyncio.Event() for _ in range(3)]
        order = []
        third = Status()
        a = await self.start(queue, 'a', gates[0], order)
        b = await self.start(queue, 'b', gates[1], order)
        c = await self.start(queue, 'c', gates[2], order, third)
        self.assertEqual(order, [('start', 'a'), ('start', 'b')])
        self.assertEqual(third.texts, [rq.QUEUED])
        self.assertEqual(queue.waiting, 1)
        gates[0].set()
        await a
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(order[-1], ('start', 'c'))
        self.assertEqual(third.texts, [rq.QUEUED, 'Читаю…'])
        gates[1].set(); gates[2].set()
        await asyncio.gather(b, c)
        self.assertEqual(queue.waiting, 0)

    async def test_photo_with_a_free_slot_gets_no_queue_message(self):
        queue = rq.RecognitionQueue()
        status = Status()
        async with queue.turn(status, 'Читаю…'):
            pass
        self.assertEqual(status.texts, [])

    async def test_queue_is_first_come_first_served(self):
        queue = rq.RecognitionQueue()
        gates = {n: asyncio.Event() for n in 'abcde'}
        order = []
        tasks = [await self.start(queue, n, gates[n], order) for n in 'abcde']
        for n in 'abcde':
            gates[n].set()
        await asyncio.gather(*tasks)
        self.assertEqual([n for kind, n in order if kind == 'start'], list('abcde'))

    async def test_too_long_queue_is_refused_with_the_agreed_text(self):
        queue = rq.RecognitionQueue()
        gate = asyncio.Event()
        order = []
        tasks = [await self.start(queue, str(i), gate, order) for i in range(2 + rq.MAX_WAITING)]
        self.assertEqual(queue.waiting, rq.MAX_WAITING)
        with self.assertRaises(rq.QueueFull) as caught:
            async with queue.turn(Status(), 'x'):
                pass
        self.assertEqual(str(caught.exception), rq.TOO_LONG)
        self.assertIsInstance(caught.exception, vision.VisionError)
        gate.set()
        await asyncio.gather(*tasks)
        self.assertEqual(queue.waiting, 0)

    async def test_stalled_queue_gives_up_and_frees_everything(self):
        queue = rq.RecognitionQueue()
        gate = asyncio.Event()
        order = []
        holders = [await self.start(queue, n, gate, order) for n in 'ab']
        with patch.object(rq, 'MAX_QUEUE_WAIT', 0.05):
            with self.assertRaises(rq.QueueStalled) as caught:
                async with queue.turn(Status(), 'x'):
                    self.fail('must not start')
        self.assertEqual(str(caught.exception), rq.STALLED)
        self.assertEqual(queue.waiting, 0)
        gate.set()
        await asyncio.gather(*holders)
        async with queue.turn():   # слоты не потеряны
            pass

    async def test_cancelled_waiter_does_not_leak_a_slot_or_a_place(self):
        queue = rq.RecognitionQueue()
        gate = asyncio.Event()
        order = []
        holders = [await self.start(queue, n, gate, order) for n in 'ab']
        waiter = await self.start(queue, 'w', gate, order)
        self.assertEqual(queue.waiting, 1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(queue.waiting, 0)
        gate.set()
        await asyncio.gather(*holders)
        async with queue.turn():
            pass

    async def test_failed_status_edit_never_breaks_reading(self):
        queue = rq.RecognitionQueue()
        gate = asyncio.Event()
        order = []
        holders = [await self.start(queue, n, gate, order) for n in 'ab']
        broken = Status()
        broken.edit_text = AsyncMock(side_effect=RuntimeError('message gone'))
        third = await self.start(queue, 'c', gate, order, broken)
        gate.set()
        await asyncio.gather(*holders, third)
        self.assertIn(('end', 'c'), order)

    async def test_each_job_gets_one_shared_time_budget(self):
        queue = rq.RecognitionQueue()
        self.assertIsNone(vision.job_deadline.get())
        self.assertEqual(vision.job_seconds_left(180), 180)
        async with queue.turn():
            left = vision.job_seconds_left(180)
            self.assertGreater(left, 180)
            self.assertLessEqual(left, rq.JOB_BUDGET)
        self.assertIsNone(vision.job_deadline.get())

    def test_agreed_texts_are_exactly_these(self):
        self.assertEqual(rq.QUEUED, 'Твоё фото в очереди — прочитаю, как только дойдёт очередь. Ничего делать не нужно.')
        self.assertEqual(rq.TOO_LONG, 'Сейчас очередь длинная. Пришли фото чуть позже, ничего не записал.')
        self.assertEqual(rq.STALLED, 'Очередь затянулась, поэтому фото не прочитал. Ничего не записал. Пришли его ещё раз чуть позже.')


if __name__ == '__main__':
    unittest.main()
