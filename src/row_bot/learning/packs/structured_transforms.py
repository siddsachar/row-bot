"""Pack: structured transforms (D30), visible split.

Each task turns one or two input files (CSV, JSON, INI, plain text) into an output
file of a stated shape. The verifier parses the output (JSON by value, CSV by rows,
text by lines), so formatting choices that don't change the data pass.
"""

from __future__ import annotations

import json

from row_bot.learning.packs import PackTask, Solution, Verifier


def _csv(rows: list[list[object]]) -> str:
    return "".join(",".join(str(cell) for cell in row) + "\n" for row in rows)


def _json(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


def _task(number: int, prompt: str, files: dict[str, str], path: str, match: str, expected: object,
          wrong: str) -> PackTask:
    text = expected if isinstance(expected, str) else _json(expected)
    stored = json.loads(text) if match == "json" else text
    return PackTask(
        id=f"st_{number:02d}", pack="structured_transforms", split="visible",
        prompt=prompt + " Write only that file, then reply with: done",
        verifier=Verifier("file", stored, match=match, path=path),
        reference=Solution(answer="done", files={path: text}), decoy=Solution(answer="done", files={path: wrong}),
        files=files, tools=("files",), max_steps=8,
    )


PEOPLE = [["name", "email", "team"], ["Ruth Okafor", "ruth@example.test", "design"],
          ["Ari Cohen", "ari@example.test", "ops"], ["Mei Tan", "mei@example.test", "design"],
          ["Bo Lind", "bo@example.test", "sales"]]
EXPENSES = [["date", "category", "amount"], ["2026-03-01", "travel", "120.50"], ["2026-03-02", "meals", "18.20"],
            ["2026-03-02", "travel", "64.00"], ["2026-03-04", "office", "42.75"], ["2026-03-05", "meals", "23.30"],
            ["2026-03-07", "travel", "15.50"]]
CONFIG = {"service": "atlas", "timeout": 30, "retry": {"count": 3, "timeout": 5},
          "workers": [{"name": "a", "timeout": 10}, {"name": "b"}]}
NOTES = ("Kick-off was on 3 March 2026 with the whole team.\n"
         "The first review is set for 2026-03-05, and the demo for March 7, 2026.\n"
         "We close the sprint on 13/03/2026 (day/month/year).\n")
INVENTORY = [{"sku": "B-220", "name": "Bolts", "qty": 140}, {"sku": "W-004", "name": "Washers", "qty": 6},
             {"sku": "N-310", "name": "Nuts", "qty": 9}, {"sku": "S-115", "name": "Screws", "qty": 10},
             {"sku": "A-001", "name": "Anchors", "qty": 0}]
CONTACTS = ("Name: Ruth Okafor / Phone: (555) 010-1234\n"
            "Name: Ari Cohen / Phone: 555.010.9876\n"
            "Name: Mei Tan / Phone: +1 555 010 4455\n")
SETTINGS_INI = "[server]\nhost = 0.0.0.0\nport = 8080\n\n[log]\nlevel = info\nfile = app.log\n"
ORDERS = [["order_id", "customer", "amount", "status"], ["1001", "acme", "250", "paid"],
          ["1002", "globex", "90", "pending"], ["1003", "acme", "40", "paid"], ["1004", "initech", "300", "paid"],
          ["1005", "globex", "60", "paid"], ["1006", "initech", "75", "refunded"]]
LOG = ("2026-03-01 12:00:01 INFO started\n"
       "2026-03-01 12:00:05 ERROR db timeout\n"
       "2026-03-01 12:01:00 WARN slow request\n"
       "2026-03-01 12:02:13 ERROR cache miss storm\n"
       "2026-03-01 12:03:40 ERROR db timeout\n"
       "2026-03-01 12:04:02 ERROR db timeout\n")
GRADES = [["student", "subject", "score"], ["noor", "math", "88"], ["leo", "math", "71"], ["noor", "art", "93"],
          ["leo", "art", "80"], ["ivy", "math", "95"], ["leo", "music", "77"]]
POSTS = [{"id": 3, "tags": ["python", "testing"]}, {"id": 1, "tags": ["python"]},
         {"id": 2, "tags": ["design", "testing"]}, {"id": 4, "tags": []}]
LEFT = [["id", "name"], ["3", "Cedar"], ["1", "Aspen"], ["4", "Dogwood"], ["2", "Birch"]]
RIGHT = [["id", "city"], ["2", "Oslo"], ["4", "Lima"], ["5", "Kyoto"], ["1", "Quito"]]
TODO = ("# Launch checklist\n- [x] Book the venue\n- [ ] Print badges\n- [x] Send invitations\n"
        "- [ ] Order coffee for 40\n- [ ] Test the projector\n")
MATRIX = [["", "q1", "q2", "q3"], ["north", "10", "12", "9"], ["south", "7", "11", "15"]]
WEIGHTS = [["item", "weight_lb"], ["anvil", "100"], ["kettlebell", "35"], ["parcel", "2.5"]]
EVENTS = [{"name": "Standup", "start": "09:15"}, {"name": "Lunch talk", "start": "12:30"},
          {"name": "Code review", "start": "10:00"}, {"name": "Retro", "start": "16:45"}]


def _totals() -> str:
    sums: dict[str, float] = {}
    for _date, category, amount in EXPENSES[1:]:
        sums[category] = sums.get(category, 0.0) + float(amount)
    return _csv([["category", "total"]] + [[name, f"{sums[name]:.2f}"] for name in sorted(sums)])


def _renamed(value: object) -> object:
    if isinstance(value, dict):
        return {("timeout_seconds" if key == "timeout" else key): _renamed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_renamed(item) for item in value]
    return value


def _paid() -> dict[str, float]:
    totals: dict[str, float] = {}
    for _order, customer, amount, status in ORDERS[1:]:
        if status == "paid":
            totals[customer] = totals.get(customer, 0) + int(amount)
    return totals


def _averages() -> str:
    scores: dict[str, list[int]] = {}
    for student, _subject, score in GRADES[1:]:
        scores.setdefault(student, []).append(int(score))
    return _csv([["student", "average"]] + [[name, f"{sum(values) / len(values):.1f}"]
                                            for name, values in sorted(scores.items())])


def _tag_index() -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for post in POSTS:
        for tag in post["tags"]:
            index.setdefault(tag, []).append(post["id"])
    return {tag: sorted(ids) for tag, ids in sorted(index.items())}


def _merged() -> str:
    names = {row[0]: row[1] for row in LEFT[1:]}
    cities = {row[0]: row[1] for row in RIGHT[1:]}
    both = sorted(set(names) & set(cities), key=int)
    return _csv([["id", "name", "city"]] + [[key, names[key], cities[key]] for key in both])


def _transposed() -> str:
    return _csv([list(column) for column in zip(*MATRIX)])


def _kilograms() -> str:
    return _csv([["item", "weight_kg"]] + [[item, f"{float(pounds) * 0.45359237:.2f}"] for item, pounds in WEIGHTS[1:]])


TASKS: tuple[PackTask, ...] = (
    _task(1, "Read people.csv from the workspace and write people.json: a JSON array of objects with the keys "
             "name and email (no other keys), sorted by name.", {"people.csv": _csv(PEOPLE)}, "people.json", "json",
          sorted(({"name": row[0], "email": row[1]} for row in PEOPLE[1:]), key=lambda item: item["name"]),
          _json([{"name": row[0], "email": row[1]} for row in PEOPLE[1:]])),
    _task(2, "Read expenses.csv and write totals.csv with the header category,total and one row per category "
             "with the sum of its amounts (two decimals), sorted by category.", {"expenses.csv": _csv(EXPENSES)},
          "totals.csv", "csv", _totals(), "category,total\nmeals,41.50\noffice,42.75\ntravel,184.50\n"),
    _task(3, "Read config.json and write config_new.json: the same JSON with every key named timeout renamed to "
             "timeout_seconds, at every level, and nothing else changed.", {"config.json": _json(CONFIG)},
          "config_new.json", "json", _renamed(CONFIG),
          _json({**CONFIG, "timeout_seconds": 30, "timeout": None})),
    _task(4, "Read notes.txt and write dates.txt with every date in it as YYYY-MM-DD, one per line, in the order "
             "they appear.", {"notes.txt": NOTES}, "dates.txt", "lines",
          "2026-03-03\n2026-03-05\n2026-03-07\n2026-03-13\n", "2026-03-03\n2026-03-05\n2026-03-07\n"),
    _task(5, "Read inventory.json and write low_stock.csv with the header sku,qty listing the items whose qty is "
             "below 10, sorted by sku.", {"inventory.json": _json(INVENTORY)}, "low_stock.csv", "csv",
          _csv([["sku", "qty"]] + [[item["sku"], item["qty"]] for item in sorted(INVENTORY, key=lambda i: i["sku"])
                                   if int(item["qty"]) < 10]),
          "sku,qty\nA-001,0\nN-310,9\nS-115,10\nW-004,6\n"),
    _task(6, "Read contacts.txt and write phones.csv with the header name,phone and one row per contact in the "
             "same order, where phone keeps only its digits.", {"contacts.txt": CONTACTS}, "phones.csv", "csv",
          "name,phone\nRuth Okafor,5550101234\nAri Cohen,5550109876\nMei Tan,15550104455\n",
          "name,phone\nRuth Okafor,(555) 010-1234\nAri Cohen,555.010.9876\nMei Tan,+1 555 010 4455\n"),
    _task(7, "Convert settings.ini to settings.json: a JSON object with one key per section, each an object of "
             "that section's keys and values, all values as strings.", {"settings.ini": SETTINGS_INI},
          "settings.json", "json", {"server": {"host": "0.0.0.0", "port": "8080"},
                                    "log": {"level": "info", "file": "app.log"}},
          _json({"server": {"host": "0.0.0.0", "port": 8080}, "log": {"level": "info", "file": "app.log"}})),
    _task(8, "Read orders.csv and write paid_orders.json: a JSON object mapping each customer to the total amount "
             "of their paid orders, as numbers. Leave out customers with no paid orders.",
          {"orders.csv": _csv(ORDERS)}, "paid_orders.json", "json", _paid(),
          _json({"acme": 290, "globex": 150, "initech": 375})),
    _task(9, "Read app.log and write error_counts.json: a JSON object mapping each ERROR message (the text after "
             "ERROR) to how many times it appears.", {"app.log": LOG}, "error_counts.json", "json",
          {"db timeout": 3, "cache miss storm": 1}, _json({"db timeout": 3})),
    _task(10, "Read grades.csv and write averages.csv with the header student,average, where average is the "
              "student's mean score with one decimal, sorted by student.", {"grades.csv": _csv(GRADES)},
          "averages.csv", "csv", _averages(), "student,average\nivy,95.0\nleo,75.5\nnoor,90.5\n"),
    _task(11, "Read posts.json and write tag_index.json: a JSON object mapping each tag to the sorted list of the "
              "ids of the posts that have it.", {"posts.json": _json(POSTS)}, "tag_index.json", "json",
          _tag_index(), _json({"python": [3, 1], "testing": [3, 2], "design": [2]})),
    _task(12, "Join trees.csv and cities.csv on id and write merged.csv with the header id,name,city, keeping only "
              "ids found in both files, sorted by id as a number.",
          {"trees.csv": _csv(LEFT), "cities.csv": _csv(RIGHT)}, "merged.csv", "csv", _merged(),
          "id,name,city\n1,Aspen,Quito\n2,Birch,Oslo\n3,Cedar,\n4,Dogwood,Lima\n"),
    _task(13, "Read todo.md and write open_items.txt listing the text of every unchecked item, one per line, in "
              "order.", {"todo.md": TODO}, "open_items.txt", "lines",
          "Print badges\nOrder coffee for 40\nTest the projector\n",
          "Book the venue\nSend invitations\n"),
    _task(14, "Read matrix.csv and write transposed.csv: the same table transposed, so its first row becomes the "
              "first column.", {"matrix.csv": _csv(MATRIX)}, "transposed.csv", "csv", _transposed(),
          _csv(MATRIX)),
    _task(15, "Read weights.csv and write weights_kg.csv with the header item,weight_kg, converting pounds to "
              "kilograms (1 lb = 0.45359237 kg) rounded to two decimals, in the same order.",
          {"weights.csv": _csv(WEIGHTS)}, "weights_kg.csv", "csv", _kilograms(),
          "item,weight_kg\nanvil,45.00\nkettlebell,15.75\nparcel,1.25\n"),
    _task(16, "Read events.json and write schedule.txt with one line per event, as 'HH:MM name', sorted by start "
              "time.", {"events.json": _json(EVENTS)}, "schedule.txt", "lines",
          "".join(f"{event['start']} {event['name']}\n" for event in sorted(EVENTS, key=lambda e: e["start"])),
          "".join(f"{event['start']} {event['name']}\n" for event in EVENTS)),
)
