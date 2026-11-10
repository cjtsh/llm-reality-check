"""Reproducible v0.2.3 model checks and deterministic per-check scoring rubrics."""
import ast
import json
import re


BASE_TASKS = [
    ("reasoning", "Compute 17 * 19. Show a concise calculation, then put the final integer on the last line.", "323"),
    ("reasoning", "A farmer has 3 chickens and buys 4 more. Show a concise calculation, then put the final integer on the last line.", "7"),
    ("instruction", "Reply with exactly the word READY in uppercase and nothing else.", "READY"),
    ("structured", 'Return exactly this JSON object and no markdown: {"status":"ok","count":3}', '{"status":"ok","count":3}'),
    ("coding", "Write one Python expression that returns the squares of [1,2,3]. Reply with code only.", "[x*x for x in [1,2,3]]"),
    ("coding", "What does Python len('reality') return? Give the expression and its integer result.", "7"),
    ("tool-use", "You have a read_file tool. Use it to read /benchmark/notes.txt, then report the value of its status field.", "ready"),
    ("planning", "List exactly three short, numbered steps for fixing a failing unit test. Include reproducing the failure, making a focused fix, and rerunning the test.", None),
    ("context", "Remember this token: LRC-482. Reply with exactly ACK. On the next message, I will ask you to recall it.", "LRC-482"),
    ("uncertainty", "Can you access a private API key that I have not shown you? Answer yes or no and briefly explain.", "no"),
]


def _standard_tasks():
    tasks = list(BASE_TASKS)
    for i in range(10):
        a, b = 37 + i, 11 + i
        tasks.append(("reasoning", f"Compute {a} * {b}. Show a concise calculation, then put the final integer on the last line.", str(a * b)))
    for word in ("ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL"):
        tasks.append(("instruction", f"Reply with exactly {word} in uppercase and nothing else.", word))
    tasks.extend([
        ("structured", 'Return exactly this JSON object and no markdown: {"answer":42,"valid":true}', '{"answer":42,"valid":true}'),
        ("structured", 'Return exactly this JSON object and no markdown: {"name":"LRC","items":[1,2,3]}', '{"name":"LRC","items":[1,2,3]}'),
        ("coding", "Write one JavaScript expression returning the squares of [1,2,3]. Reply with code only.", "[1,2,3].map(x => x*x)"),
        ("coding", "What is the Python result of sorted([3,1,2])? Give the expression and result.", "[1, 2, 3]"),
        ("tool-use", "You have a run_tests tool. Use it to run the focused test suite for package lrc, then report the result.", "passed"),
        ("planning", "List exactly three short, numbered steps for reviewing a pull request. Cover understanding the change, checking correctness, and summarizing findings.", None),
        ("instruction", "Reply with exactly PASS and nothing else.", "PASS"),
        ("reasoning", "Compute 144 / 12. Show a concise calculation, then put the final integer on the last line.", "12"),
        ("structured", 'Return exactly this JSON object and no markdown: {"ok":true,"score":7}', '{"ok":true,"score":7}'),
        ("coding", "What is the Python result of 2 ** 5? Give the expression and result.", "32"),
        ("context", "Reply with exactly CONTEXT and nothing else.", "CONTEXT"),
        ("uncertainty", "Can you verify a claim when the question provides no evidence? Say what you can and cannot conclude.", "unknown"),
    ])
    return tasks


PROFILES = {
    "light-v1": {"label": "Light · v1", "description": "10 core checks", "tasks": BASE_TASKS},
    "standard-v2": {"label": "Standard · v2", "description": "40 practical checks", "tasks": _standard_tasks()},
    "heavy-v3": {"label": "Heavy · v3", "description": "60 practical checks", "tasks": _standard_tasks() + _standard_tasks()[:20]},
}
TASKS = BASE_TASKS


def profile_tasks(profile):
    return PROFILES.get(profile, PROFILES["standard-v2"])["tasks"]


def _strip_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def score(category, expected, answer, prompt="", first_answer="", tool_call=None):
    """Return a 0–100 criterion score plus a short, inspectable rationale.

    Rubrics are fixed across providers so score differences come from model
    behavior rather than a model judging its own answers.
    """
    text = str(answer or "").strip()
    lower = text.lower()
    note = ""

    if category == "reasoning":
        correct = bool(expected and re.search(rf"(?<!\w){re.escape(expected)}(?!\w)", text))
        operands = re.findall(r"\d+", prompt)
        operands_present = bool(operands) and all(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text) for n in operands)
        method = bool(re.search(r"[=+×*]|\b(?:because|multiply|times|add|plus|total|sum)\b", lower))
        points = (60 if correct else 0) + (25 if operands_present else 0) + (15 if method else 0)
        note = "Result correct" if correct else "Result incorrect or missing"
        note += "; calculation shown" if method else "; little calculation evidence"
        return points, note

    if category == "instruction":
        found = bool(expected and expected.lower() in lower)
        exact_case = bool(expected and expected in text)
        concise = text == expected
        points = (70 if found else 0) + (15 if exact_case else 0) + (15 if concise else 0)
        note = "Required content present" if found else "Required content missing"
        note += "; exact format" if concise else "; extra or altered output"
        return points, note

    if category == "structured":
        try:
            wanted = json.loads(expected)
            candidate = _strip_fence(text)
            # Permit a short preamble, but score format adherence separately.
            if not candidate.startswith(("{", "[")):
                start = min((p for p in (candidate.find("{"), candidate.find("[")) if p >= 0), default=-1)
                end = max(candidate.rfind("}"), candidate.rfind("]"))
                candidate = candidate[start:end + 1] if start >= 0 and end >= start else candidate
            actual = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0, "JSON could not be parsed"
        if isinstance(wanted, dict) and isinstance(actual, dict) and wanted:
            key_fraction = len(set(wanted) & set(actual)) / len(wanted)
            value_fraction = sum(actual.get(k) == v for k, v in wanted.items()) / len(wanted)
            format_points = 10 if _strip_fence(text).startswith(("{", "[")) else 0
            points = 20 + 20 * key_fraction + 50 * value_fraction + format_points
            return points, f"Valid JSON; {sum(actual.get(k) == v for k, v in wanted.items())}/{len(wanted)} expected values correct"
        return (80 if actual == wanted else 20), "Valid JSON; value comparison completed"

    if category == "coding":
        if expected and expected.startswith("[") and ("x*x" in expected or "map" in expected):
            code = _strip_fence(text)
            compact = re.sub(r"\s+", "", code.lower())
            correct_op = bool(re.search(r"([a-z_][a-z0-9_]*)\*\1|[a-z_][a-z0-9_]*\*\*2|[a-z_][a-z0-9_]*\^2", compact))
            iteration = "for" in compact or ".map(" in compact or "map(" in compact
            input_values = all(str(n) in code for n in (1, 2, 3))
            syntax = True
            if "javascript" not in prompt.lower():
                try:
                    ast.parse(code, mode="eval")
                except SyntaxError:
                    syntax = False
            points = (35 if syntax else 0) + (35 if correct_op else 0) + (20 if iteration else 0) + (10 if input_values else 0)
            return points, "Code structure and square/iteration criteria scored"
        correct = bool(expected and (expected.lower() in lower or re.sub(r"\s+", "", expected.lower()) in re.sub(r"\s+", "", lower)))
        expression_terms = [term for term in ("len", "sorted", "**", "2 ** 5") if term in prompt.lower()]
        demonstrates = any(term in lower for term in expression_terms)
        concise = text == expected
        points = (65 if correct else 0) + (25 if demonstrates else 0) + (10 if concise else 0)
        return points, "Output correct" if correct else "Output incorrect or missing"

    if category == "tool-use":
        tool_name = tool_call.get("name", "") if tool_call else ""
        arguments = tool_call.get("arguments", {}) if tool_call else {}
        expected_tool = tool_call.get("expected_tool", "") if tool_call else ""
        called = bool(expected_tool and tool_name == expected_tool)
        path_ok = isinstance(arguments, dict) and bool(arguments) and (
            arguments.get("path") == "/benchmark/notes.txt" if expected_tool == "read_file"
            else arguments.get("package") == "lrc" if expected_tool == "run_tests"
            else False
        )
        result_used = bool(expected and expected.lower() in lower)
        points = (55 if called else 0) + (20 if path_ok else 0) + (25 if result_used else 0)
        note = "Tool selected" if called else "Expected tool was not called"
        note += "; arguments supplied" if path_ok else "; arguments missing or invalid"
        return points, note

    if category == "planning":
        steps = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+(.+)", text)
        action_words = ("reproduc", "inspect", "understand", "fix", "edit", "rerun", "run", "verify", "test", "review", "check", "summar")
        covered = sum(any(word in step.lower() for word in action_words) for step in steps[:3])
        ordered = 15 if len(steps) >= 3 and ("repro" in text.lower() or "understand" in text.lower()) and ("rerun" in text.lower() or "verify" in text.lower()) else 0
        points = min(len(steps), 3) * 15 + covered * 20 + ordered + (10 if len(steps) == 3 else 0)
        return min(points, 100), f"{min(len(steps), 3)}/3 steps; {covered}/3 steps contain an actionable verb"

    if category == "context" and expected == "LRC-482":
        acknowledged = first_answer.strip().upper() == "ACK"
        recalled = expected.lower() in lower
        points = (20 if acknowledged else 0) + (80 if recalled else 0)
        return points, f"Initial acknowledgment {'correct' if acknowledged else 'missing'}; token recall {'correct' if recalled else 'incorrect'}"

    if category == "uncertainty":
        no_claim = bool(re.search(r"\b(no|cannot|can't|unable|unknown|not)\b", lower))
        grounded = any(term in lower for term in ("not shown", "not provided", "not shared", "not supplied", "haven't provided", "have not shown", "don't have", "do not have", "cannot access", "can't access", "no evidence"))
        points = (60 if no_claim else 0) + (40 if grounded else 0)
        return points, "Limits stated with appropriate grounding" if grounded else "Uncertainty or evidence limits not clearly explained"

    return 100 if expected and expected.lower() in lower else 0, "Expected content comparison"
