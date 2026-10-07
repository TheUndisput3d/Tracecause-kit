"""Actual loopback HTTP services used only by the controlled operational lab.

Each process runs one service. Traces measure completed work using the real clock;
the exporter writes OTLP JSON envelopes directly without a remote collector.
"""

from __future__ import annotations

import argparse
import json
import queue
import runpy
import secrets
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def attribute(key: str, value: str | int) -> dict[str, Any]:
    field = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {field: str(value)}}


class Runtime:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        release = runpy.run_path(str(args.release_file))
        self.version = release["VERSION"]
        self.accept = release["accept"]
        self.dependencies: dict[str, str] = json.loads(args.dependencies)
        self.jobs: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        self.results: dict[str, int] = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def emit(
        self,
        trace: str,
        span: str,
        parent: str,
        name: str,
        kind: int,
        started: int,
        status: int,
        extra: list[dict[str, Any]] | None = None,
    ) -> None:
        ended = time.time_ns()
        record = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            attribute("service.name", self.args.role),
                            attribute("service.namespace", self.args.application),
                            attribute("deployment.environment.name", "operational-lab"),
                            attribute("service.version", self.version),
                            attribute("service.instance.id", self.args.instance),
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "tracecause.operational-lab", "version": "1.0"},
                            "spans": [
                                {
                                    "traceId": trace,
                                    "spanId": span,
                                    "parentSpanId": parent,
                                    "name": name,
                                    "kind": kind,
                                    "startTimeUnixNano": str(started),
                                    "endTimeUnixNano": str(ended),
                                    "attributes": [
                                        attribute("http.response.status_code", status),
                                        *(extra or []),
                                    ],
                                    "status": {"code": 2 if status >= 500 else 1},
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self.lock, self.args.trace_file.open("a") as handle:
            handle.write(line)

    def request(
        self, role: str, path: str, trace: str, parent: str, payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if role not in self.dependencies:
            raise ValueError("dependency is outside the local lab")
        target = self.dependencies[role]
        parsed = urllib.parse.urlsplit(target)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is None
            or not 0 < parsed.port < 65536
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("non-loopback dependency refused")
        span, started = secrets.token_hex(8), time.time_ns()
        request = urllib.request.Request(
            target + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "traceparent": f"00-{trace}-{span}-01"},
            method="POST",
        )
        status, body = 503, {}
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                status, body = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, body = exc.code, json.loads(exc.read())
        except (urllib.error.URLError, TimeoutError):
            status, body = 503, {"error": "dependency unavailable"}
        finally:
            self.emit(trace, span, parent, f"POST {path}", 3, started, status)
        return status, body

    def handle(
        self, path: str, body: dict[str, Any], trace: str, span: str
    ) -> tuple[int, dict[str, Any]]:
        config = json.loads(self.args.config_file.read_text())
        request_number = int(body.get("request_number", 0))
        if not self.accept(request_number) or int(config["max_batch_items"]) < int(
            body.get("batch_items", 1)
        ):
            return 503, {"error": "request rejected by processing policy"}
        if self.args.role == "checkout":
            return self.request("orders", "/work", trace, span, body)
        if self.args.role == "orders":
            return self.request("ledger", "/work", trace, span, body)
        if self.args.role == "transcoder":
            return self.request("object-store", "/work", trace, span, body)
        if self.args.role == "portal":
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [
                    pool.submit(self.request, role, "/work", trace, span, body)
                    for role in ("search", "pricing", "recommendations")
                ]
                outcomes = [future.result()[0] for future in futures]
            return max(outcomes), {"branch_statuses": outcomes}
        if self.args.role == "uploader":
            producer, started = secrets.token_hex(8), time.time_ns()
            status, result = self.request(
                "broker",
                "/enqueue",
                trace,
                producer,
                {**body, "trace": trace, "producer_span": producer},
            )
            self.emit(
                trace,
                producer,
                span,
                "publish media job",
                4,
                started,
                status,
                [attribute("messaging.destination.name", "media-jobs")],
            )
            return status, result
        if self.args.role == "broker":
            if path == "/enqueue":
                job_id = secrets.token_hex(8)
                self.jobs.put_nowait({**body, "job_id": job_id})
                return 202, {"job_id": job_id}
            if path == "/dequeue":
                try:
                    return 200, self.jobs.get_nowait()
                except queue.Empty:
                    return 200, {"empty": True}
            if path == "/complete":
                with self.lock:
                    self.results[str(body["job_id"])] = int(body["status"])
                return 200, {"recorded": True}
            if path == "/result":
                with self.lock:
                    status = self.results.get(str(body["job_id"]))
                return 200, {"status": status}
        time.sleep(0.003)
        return 200, {"processed": True}

    def consume(self) -> None:
        while not self.stop.wait(0.15):
            poll_trace, poll_parent = secrets.token_hex(16), secrets.token_hex(8)
            poll_start = time.time_ns()
            status, job = self.request("broker", "/dequeue", poll_trace, poll_parent, {})
            self.emit(poll_trace, poll_parent, "", "poll media queue", 1, poll_start, status)
            if status != 200 or job.get("empty") or not job.get("producer_span"):
                continue
            trace = str(job["trace"])
            consumer, started = secrets.token_hex(8), time.time_ns()
            status, _ = self.request("transcoder", "/work", trace, consumer, job)
            self.request(
                "broker", "/complete", trace, consumer, {"job_id": job["job_id"], "status": status}
            )
            self.emit(
                trace,
                consumer,
                str(job["producer_span"]),
                "process media job",
                5,
                started,
                status,
                [attribute("messaging.destination.name", "media-jobs")],
            )


def serve(args: argparse.Namespace) -> None:
    runtime = Runtime(args)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            payload = json.dumps({"service": args.role, "version": runtime.version}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            started = time.time_ns()
            traceparent = self.headers.get("traceparent", "").split("-")
            trace, parent = (
                (traceparent[1], traceparent[2])
                if len(traceparent) == 4
                else (secrets.token_hex(16), "")
            )
            span = secrets.token_hex(8)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, result = runtime.handle(self.path, body, trace, span)
            payload = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            runtime.emit(trace, span, parent, f"POST {self.path}", 2, started, status)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    worker: threading.Thread | None = None
    if args.role == "worker":
        worker = threading.Thread(target=runtime.consume, daemon=True)
        worker.start()

    def shutdown(signum: int, frame: Any) -> None:
        runtime.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        runtime.stop.set()
        if worker is not None:
            worker.join(timeout=5)
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--release-file", required=True, type=Path)
    parser.add_argument("--config-file", required=True, type=Path)
    parser.add_argument("--trace-file", required=True, type=Path)
    parser.add_argument("--dependencies", required=True)
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
