"""Run the upstream Inspect tasks in a separate process from the dashboard."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from inspect_ai import eval as inspect_eval
from inspect_ai.event._model import ModelEvent, model_event_progress
from inspect_ai.hooks import Hooks, hooks
from inspect_ai.log._samples import active_samples
from inspect_ai.model import get_model
from inspect_ai.scorer import value_to_float

from .benchmark import Selection, check_outcome, profile_tasks


def emit(**event):
    print("LRC " + json.dumps(event), flush=True)


@hooks("dashboard_progress", "Report live check and provider activity to the dashboard")
class DashboardProgress(Hooks):
    stream_observer: asyncio.Task | None = None

    async def on_sample_start(self, data):
        self.stop_observer()
        self.stream_observer = asyncio.create_task(self.observe_stream(data.sample_id))

    async def observe_stream(self, sample_id):
        # Inspect is pinned. Its control channel reads the same pending model events.
        last_progress = None
        while True:
            await asyncio.sleep(1)
            for sample in active_samples():
                if sample.sample_uuid != sample_id:
                    continue
                for event in sample.transcript.pending_events:
                    if not isinstance(event, ModelEvent):
                        continue
                    progress = model_event_progress(event)
                    if progress and progress.last_progress_at != last_progress:
                        last_progress = progress.last_progress_at
                        emit(stream_progress=True, stream_tokens=progress.output_tokens)

    def stop_observer(self):
        if self.stream_observer is not None:
            self.stream_observer.cancel()
            self.stream_observer = None

    async def on_run_end(self, data):
        self.stop_observer()

    async def on_sample_init(self, data):
        emit(phase="Preparing check", sample=str(data.summary.id))

    async def on_before_model_generate(self, data):
        emit(phase="Waiting for model response", reset_stream=True)

    async def on_model_retry(self, data):
        reason = f"HTTP {data.status_code}" if data.status_code else (data.exception_type or "provider error")
        emit(phase=f"Retrying after {reason} in {data.wait_time:.1f}s")

    async def on_sample_event(self, data):
        if data.event.event == "model" and not data.event.error:
            emit(phase="Model responded")

    async def on_sample_scoring(self, data):
        emit(phase="Scoring check")

    async def on_sample_end(self, data):
        self.stop_observer()
        sample = data.sample
        outcome = "ungraded"
        if not sample.error and len(sample.scores or {}) == 1:
            result = next(iter(sample.scores.values()))
            grade = value_to_float()(result.value)
            outcome = check_outcome(grade * 100)
        labels = {
            "passed": "Check passed", "failed": "Check failed",
            "partial": "Check earned partial credit", "ungraded": "Check ended without a grade",
        }
        emit(
            phase=labels[outcome], sample=str(sample.id), finished=True,
            outcome=outcome, duration=sample.total_time,
            tps=sample_speed(sample)[1] if outcome != "ungraded" else None,
        )


def upstream_task(source: str):
    if source == "gpqa_diamond":
        from inspect_evals.gpqa import gpqa_diamond

        return gpqa_diamond(shuffle_choices=42)
    if source == "humaneval":
        from inspect_evals.humaneval import humaneval

        return humaneval()
    if source == "agent_bench_os":
        from inspect_evals.agent_bench import agent_bench_os

        return agent_bench_os()
    from inspect_evals.bfcl import bfcl

    categories = {
        "bfcl_single": ["simple_python", "multiple", "parallel", "irrelevance"],
        "bfcl_multi": ["multi_turn_base", "multi_turn_miss_func", "multi_turn_miss_param", "multi_turn_long_context"],
    }
    return bfcl(categories=categories[source])


def docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=10,
        ).returncode == 0 and subprocess.run(
            ["docker", "compose", "version"], capture_output=True, timeout=10,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def sample_speed(sample):
    calls = [event for event in sample.events if event.event == "model"]
    timed = [event.output.time for event in calls]
    usages = [event.output.usage for event in calls]
    tokens = sum(usage.output_tokens for usage in sample.model_usage.values())
    tps = None
    if calls and all(t is not None and t > 0 for t in timed) and all(usage and usage.output_tokens > 0 for usage in usages):
        tps = tokens / sum(timed)
    return tokens, tps


def sample_result(selection: Selection, sample) -> dict:
    source = "bfcl" if selection.source.startswith("bfcl_") else selection.source
    if sample.error or len(sample.scores or {}) != 1:
        raise ValueError("Sample did not receive its published score")
    scorer, result = next(iter(sample.scores.items()))
    grade = value_to_float()(result.value)
    if not 0 <= grade <= 1:
        raise ValueError("Unexpected upstream score scale")

    tokens, tps = sample_speed(sample)

    prompt = sample.input
    if not isinstance(prompt, str):
        prompt = "\n".join(f"{message.role}: {message.text}" for message in prompt)
    if sample.choices:
        prompt += "\n\n" + "\n".join(f"{chr(65 + i)}. {choice}" for i, choice in enumerate(sample.choices))
    return {
        "category": selection.category,
        "prompt": prompt[:8000],
        "answer": (result.answer or sample.output.completion)[:8000],
        "score": grade * 100,
        "latency_s": sample.total_time,
        "tokens": tokens,
        "tps": tps,
        "note": f"inspect_evals/{source} · sample {sample.id} · {scorer}: {result.value}",
    }


def run_selection(selection: Selection, model, log_dir: str) -> list[dict]:
    task = upstream_task(selection.source)
    ids = set(selection.ids)
    selected = [sample.id for sample in task.dataset if sample.id in ids]
    if len(selected) != len(ids) or set(selected) != ids:
        raise ValueError("The pinned sample IDs do not match the upstream dataset")
    sandbox = None
    if selection.source == "humaneval":
        sandbox = ("docker", str(Path(__file__).with_name("humaneval-compose.yaml")))
    [log] = inspect_eval(
        task, model=model, sample_id=list(selection.ids), epochs=1, sandbox=sandbox,
        max_samples=1, max_connections=1, cache=False, display="none",
        log_dir=log_dir, log_model_api=False, score_display=False,
        fail_on_error=False, timeout=180, max_retries=1,
    )
    if log.status == "cancelled":
        raise KeyboardInterrupt
    samples = {sample.id: sample for sample in log.samples or []}
    if log.status != "success" or set(samples) != ids:
        raise ValueError("Inspect did not complete all pinned samples")
    return [sample_result(selection, samples[sample_id]) for sample_id in selection.ids]


def main():
    request = json.load(sys.stdin)
    stream = request.get("stream_activity")
    if stream is None:
        stream = urlsplit(request["endpoint"]).hostname in {"localhost", "127.0.0.1", "::1"}
    emit(stream_enabled=stream)
    model = get_model(
        f"openai-api/lrc/{request['model']}", base_url=request["endpoint"],
        api_key=request["api_key"] or "local", responses_api=False, strict_tools=False,
        # Let Inspect own retries so its activity hook can report the backoff.
        emulate_tools=False, memoize=False, max_retries=0, stream=stream,
    )
    has_docker = docker_available()
    with TemporaryDirectory(prefix="lrc-inspect-") as log_dir:
        for selection in profile_tasks(request["profile"]):
            emit(current=selection.source, phase="Preparing benchmark group")
            try:
                if selection.source in {"humaneval", "agent_bench_os"} and not has_docker:
                    emit(issue=f"{selection.source}: Docker with Compose is required.")
                    continue
                checks = run_selection(selection, model, log_dir)
                emit(checks=checks)
            except KeyboardInterrupt:
                return
            except Exception as exc:
                # Provider errors can contain request details. Report only the source and error type.
                emit(issue=f"{selection.source}: Inspect could not complete or score this group ({type(exc).__name__}).")


if __name__ == "__main__":
    main()
