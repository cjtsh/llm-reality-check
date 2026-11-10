"""Small deterministic developer/agent profile used by the v0.2.1 runner.

The task schema is deliberately simple so it can be migrated to native Inspect
tasks without changing the public run API.
"""
BASE_TASKS = [
    ("reasoning", "What is 17 * 19? Reply with only the integer.", "323"),
    ("reasoning", "A farmer has 3 chickens and buys 4 more. How many chickens? Reply with only the integer.", "7"),
    ("instruction", "Reply with exactly the word READY in uppercase and nothing else.", "READY"),
    ("structured", 'Return exactly this JSON object and no markdown: {"status":"ok","count":3}', '{"status":"ok","count":3}'),
    ("coding", "Write one Python expression that returns the squares of [1,2,3]. Reply with code only.", "[x*x for x in [1,2,3]]"),
    ("coding", "What does Python len('reality') return? Reply with only the integer.", "7"),
    ("tool-use", "Name the tool you would use to read a local file. Reply with one word.", "filesystem"),
    ("planning", "List exactly three short steps for fixing a failing unit test, numbered 1 to 3.", None),
    ("context", "Remember this token: LRC-482. Reply with only ACK.", "ACK"),
    ("uncertainty", "Can you know a private API key that I have not shown you? Reply yes or no.", "no"),
]

def _standard_tasks():
    tasks = list(BASE_TASKS)
    for i in range(10):
        tasks.append(("reasoning", f"What is {37 + i} * {11 + i}? Reply with only the integer.", str((37 + i) * (11 + i))))
    for word in ("ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL"):
        tasks.append(("instruction", f"Reply with exactly {word} in uppercase and nothing else.", word))
    tasks.extend([
        ("structured", '{"answer":42,"valid":true}', '{"answer":42,"valid":true}'),
        ("structured", '{"name":"LRC","items":[1,2,3]}', '{"name":"LRC","items":[1,2,3]}'),
        ("coding", "Write one JavaScript expression returning the squares of [1,2,3]. Code only.", "[1,2,3].map(x => x*x)"),
        ("coding", "What is the Python result of sorted([3,1,2])? Reply with only the list.", "[1, 2, 3]"),
        ("tool-use", "Name the tool you would use to run a shell command. Reply with one word.", "shell"),
        ("planning", "List exactly three short steps for reviewing a pull request, numbered 1 to 3.", None),
        ("instruction", "Reply with exactly PASS and nothing else.", "PASS"),
        ("reasoning", "What is 144 divided by 12? Reply with only the integer.", "12"),
        ("structured", '{"ok":true,"score":7}', '{"ok":true,"score":7}'),
        ("coding", "What does Python 2 ** 5 return? Reply with only the integer.", "32"),
        ("context", "Reply with exactly CONTEXT and nothing else.", "CONTEXT"),
        ("uncertainty", "Reply with exactly UNKNOWN and nothing else.", "UNKNOWN"),
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

def score(category, expected, answer):
    clean = answer.strip()
    if expected is not None:
        return 1.0 if clean == expected else 0.0
    if category == "planning":
        return 1.0 if all(f"{i}." in clean for i in (1, 2, 3)) else 0.0
    return 0.0
