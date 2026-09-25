"""Bounded market jobs with a reserved foreground lane and live progress."""
import asyncio
import logging
import time

log = logging.getLogger("market_jobs")


class MarketJobs:
    def __init__(self, runner, signature, timeout=240, max_pending=120):
        self.runner, self.signature = runner, signature
        self.timeout, self.max_pending = timeout, max_pending
        self.jobs = {}
        self.workers = []
        self.queues = [asyncio.Queue(), asyncio.Queue()]

    def start(self):
        if not self.workers:
            self.workers = [asyncio.create_task(self._worker(lane)) for lane in (0, 1)]

    async def close(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        self.jobs.clear()
        self.queues = [asyncio.Queue(), asyncio.Queue()]

    def _key(self, market_id):
        return self.signature(), market_id

    def enqueue(self, market, foreground=False, retry=False):
        key = self._key(market["id"])
        job = self.jobs.get(key)
        if job and job["state"] in {"waiting", "profiling"}:
            if foreground and job["state"] == "waiting" and job["lane"] == 0:
                job["lane"] = 1
                self.queues[1].put_nowait((key, job))
            return
        if job and job["state"] == "error" and not retry and time.monotonic() - job["finished"] < 30:
            return
        pending = sum(j["state"] in {"waiting", "profiling"} for j in self.jobs.values())
        if pending >= self.max_pending:
            victim = next((k for k, j in self.jobs.items() if j["state"] == "waiting" and j["lane"] == 0), None)
            if not foreground or victim is None:
                return
            del self.jobs[victim]  # Make room for the market the user opened.
        # Retain bounded recent progress only; completed results also live in Mongo.
        for old in list(self.jobs):
            if len(self.jobs) < 160:
                break
            if self.jobs[old]["state"] in {"ready", "error"}:
                del self.jobs[old]
        job = {"state":"waiting", "lane":int(foreground), "market":market,
            "analysis":None, "progress":{}, "finished":None}
        self.jobs[key] = job
        self.queues[job["lane"]].put_nowait((key, job))

    def latest(self, market_id):
        return (self.jobs.get(self._key(market_id)) or {}).get("analysis")

    def status(self, market_id, cached=None):
        job = self.jobs.get(self._key(market_id))
        state = job["state"] if job else ("ready" if cached else "not_started")
        result = {"state":state, "hasCachedResult":bool(cached),
            "updatedAt":(cached or {}).get("updatedAt")}
        if job:
            result.update(job["progress"])
            result["priority"] = "opened_market" if job["lane"] else "background"
            if state == "error":
                result["message"] = "Profiling could not finish. Available results are retained; retry to continue."
        return result

    def pending_count(self):
        return sum(j["state"] == "waiting" for j in self.jobs.values())

    async def _worker(self, lane):
        queue = self.queues[lane]
        while True:
            key, job = await queue.get()
            try:
                # A promoted queue entry or replaced job is never run twice.
                if self.jobs.get(key) is not job or job["lane"] != lane or job["state"] != "waiting" or key[0] != self.signature():
                    continue
                if lane == 0:
                    while any(j.get("state") == "profiling" and j.get("lane") == 1 for j in self.jobs.values()):
                        await asyncio.sleep(0.5)
                job["state"] = "profiling"
                job["progress"] = {"stage":"holders"}

                async def progress(analysis, details):
                    if key[0] == self.signature():
                        job["analysis"] = analysis
                        job["progress"] = details

                job["analysis"] = await asyncio.wait_for(self.runner(job["market"], progress), self.timeout)
                job["state"] = "ready"
                log.info("profiled %s (%s)", key[1][:10], "opened" if lane else "background")
            except asyncio.CancelledError:
                raise
            except Exception:
                job["state"] = "error"
                log.exception("Market profiling failed for %s", key[1][:10])
            finally:
                job["finished"] = time.monotonic()
                queue.task_done()
