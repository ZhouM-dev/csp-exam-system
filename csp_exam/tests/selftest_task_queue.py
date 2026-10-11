"""Persistence, bursts, duplicate jobs, claims and recovery with an isolated SQLite DB."""
import concurrent.futures
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import store,task_queue

class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=patch.object(store,'DATA_DIR',self.tmp.name);p.start();self.addCleanup(p.stop)

    def add(self,i):
        task_queue.put('c',str(i),{1:'raw.cpp'},{'problems':[]},'submission')

    def test_burst_dedup_and_restart(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(self.add,range(100)))
            list(pool.map(self.add,range(100)))
        self.assertEqual(task_queue.pending(),100)
        job=task_queue.claim()
        self.assertEqual(job[1][2],{1:'raw.cpp'})
        task_queue.recover()
        again=task_queue.claim()
        self.assertEqual(again[0],job[0])
        task_queue.complete(again[0])
        seen=set()
        while (job:=task_queue.claim()) is not None:
            self.assertNotIn(job[0],seen);seen.add(job[0]);task_queue.complete(job[0])
        self.assertEqual(len(seen),99)
        self.assertEqual(task_queue.pending(),0)

    def test_claim_is_exclusive_and_retry_is_delayed(self):
        self.add(1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs=list(pool.map(lambda _:task_queue.claim(),range(8)))
        self.assertEqual(sum(j is not None for j in jobs),1)
        job=next(j for j in jobs if j is not None)
        task_queue.complete(job[0],retry=True,attempts=0)
        self.assertIsNone(task_queue.claim())
        with patch.object(task_queue.time,'time',return_value=10**12):
            retry=task_queue.claim()
        self.assertEqual(retry[2],1)
        task_queue.complete(retry[0])
        self.assertEqual(task_queue.pending(),0)

if __name__=='__main__':unittest.main()
