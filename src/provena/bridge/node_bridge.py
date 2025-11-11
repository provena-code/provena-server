import datetime
import json, subprocess, threading, queue, os
from progsnap2.spec.enums import MainTableColumns as Cols


class NodeWorker(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.running = True
        self.proc = subprocess.Popen(
            ["node", path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1  # line-buffered
        )
        self.jobs = queue.Queue()

    def run(self):
        while self.running:
            records, future = self.jobs.get()
            self.proc.stdin.write(records + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                result = {"error": "Failed to parse JSON"}
            future.put(result)

    def stop(self):
        self.running = False
        self.proc.terminate()
        self.proc.wait()

def _convert_timestamps(rows: list[dict]) -> list[dict]:
    for row in rows:
        if Cols.ClientTimestamp in row and isinstance(row[Cols.ClientTimestamp], datetime.datetime):
            row[Cols.ClientTimestamp] = row[Cols.ClientTimestamp].isoformat()
    return rows

def process_edits(row_dict: dict[str, list[dict]], worker_count = 4) -> list[dict]:
    workers = [NodeWorker("provena/dist/App.js") for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    # Process each group of edits
    batches = []
    keys = list(row_dict.keys())
    for i in range(len(row_dict)):
        future = queue.Queue()
        rows = _convert_timestamps(row_dict[keys[i]])
        workers[i % worker_count].jobs.put((json.dumps(rows), future))
        batches.append(future)
    results = []
    for future in batches:
        results.append(future.get())
    for worker in workers:
        worker.stop()
    # convert back to a dict
    results_dict = {}
    for key, result in zip(keys, results):
        results_dict[key] = result
    return results_dict

def process_edits_group(rows: list[dict]) -> list[dict]:
    rows = _convert_timestamps(rows)
    worker = NodeWorker("provena/dist/App.js")
    worker.start()
    future = queue.Queue()
    worker.jobs.put((json.dumps(rows), future))
    result = future.get()
    worker.stop()
    return result