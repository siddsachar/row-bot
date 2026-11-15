"""Made-up data packs a Sandbox starts with (5.3.1 B4): files for its workspace, nothing real.

Every name, address, number and document here is invented for trying Row-Bot out, filming demos
and showing it to someone without anyone's own data.
"""

from __future__ import annotations

PACKS: dict[str, str] = {
    "empty": "Empty",
    "shop": "A small shop, with its own web app and inbox",
    "charity": "A charity's documents",
    "paperwork": "Household paperwork",
    "code": "Sample code",
}

FILES: dict[str, dict[str, str]] = {
    "empty": {},
    "shop": {
        "README.md": (
            "# Fernleaf Teas\n\nA made-up tea shop for trying Row-Bot. Its web app is in `app/`: open\n"
            "`app/index.html`, or run `python -m http.server 8765` in `app/` and visit localhost:8765.\n\n"
            "Customers' emails are in `inbox/`. The shop has no mail account here: a reply is saved in\n"
            "`outbox/`, named after the email it answers.\n"
        ),
        "app/index.html": (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Fernleaf Teas</title>\n<link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n"
            "<header><h1>Fernleaf Teas</h1><a href=\"#basket\" id=\"basket-link\">Basket (0)</a></header>\n"
            "<main>\n<h2>Our teas</h2>\n<ul id=\"products\"></ul>\n"
            "<section id=\"basket\"><h2>Basket</h2><ul id=\"basket-items\"></ul>\n"
            "<form id=\"checkout\"><label>Name <input name=\"name\" required></label>\n"
            "<label>Email <input name=\"email\" type=\"email\" required></label>\n"
            "<button type=\"submit\">Place order</button></form>\n<p id=\"thanks\" hidden>Thank you! "
            "Your order is on its way.</p></section>\n</main>\n<script src=\"app.js\"></script>\n</body>\n</html>\n"
        ),
        "app/style.css": (
            "body { font-family: system-ui, sans-serif; margin: 0; color: #1f2937; }\n"
            "header { display: flex; justify-content: space-between; align-items: center; padding: 16px 24px;"
            " background: #14532d; color: white; }\nheader a { color: white; }\n"
            "main { max-width: 760px; margin: 24px auto; padding: 0 16px; }\n"
            "#products { list-style: none; padding: 0; display: grid; gap: 12px;"
            " grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }\n"
            "#products li { border: 1px solid #d1d5db; border-radius: 10px; padding: 12px; }\n"
            "button { background: #14532d; color: white; border: 0; border-radius: 6px; padding: 8px 14px; }\n"
            "label { display: block; margin: 8px 0; }\n"
        ),
        "app/app.js": (
            "const teas = [\n  { name: 'Morning Mist', price: 6.5 },\n  { name: 'Fernleaf Green', price: 7.25 },\n"
            "  { name: 'Smoky Ember', price: 8 },\n  { name: 'Quiet Chamomile', price: 5.75 },\n];\n"
            "const basket = [];\nconst list = document.getElementById('products');\n"
            "teas.forEach((tea) => {\n  const item = document.createElement('li');\n"
            "  item.innerHTML = `<h3>${tea.name}</h3><p>£${tea.price.toFixed(2)}</p>`;\n"
            "  const add = document.createElement('button');\n  add.textContent = 'Add to basket';\n"
            "  add.onclick = () => { basket.push(tea); render(); };\n  item.append(add);\n"
            "  list.append(item);\n});\nfunction render() {\n"
            "  document.getElementById('basket-link').textContent = `Basket (${basket.length})`;\n"
            "  document.getElementById('basket-items').innerHTML = basket.map((t) => `<li>${t.name}</li>`).join('');\n"
            "}\ndocument.getElementById('checkout').onsubmit = (event) => {\n  event.preventDefault();\n"
            "  document.getElementById('thanks').hidden = false;\n};\n"
        ),
        "docs/returns-policy.md": (
            "# Returns\n\nUnopened tea can be returned within 30 days for a full refund. Write to "
            "hello@fernleaf.example with your order number.\n"
        ),
        "docs/orders-october.csv": (
            "order,date,customer,items,total\n1041,2026-10-02,Ada Brook,Morning Mist x2,13.00\n"
            "1042,2026-10-05,Sam Patel,Smoky Ember,8.00\n1043,2026-10-09,Lee Moreau,Fernleaf Green x3,21.75\n"
            "1044,2026-10-14,Ada Brook,Quiet Chamomile,5.75\n"
        ),
        # A customer inbox, so answering one (the obvious first thing to try) has something to answer (U15).
        "inbox/2026-10-21-where-is-my-order.md": (
            "From: Sam Patel <sam.patel@example.com>\nTo: hello@fernleaf.example\nDate: 21 Oct 2026\n"
            "Subject: Where is my order?\n\nHi, I ordered Smoky Ember on 5 October (order 1042) and it still "
            "hasn't arrived. Can you check where it is?\n\nThanks,\nSam\n"
        ),
        "inbox/2026-10-23-wrong-tea.md": (
            "From: Lee Moreau <lee.moreau@example.com>\nTo: hello@fernleaf.example\nDate: 23 Oct 2026\n"
            "Subject: Wrong tea in my parcel\n\nHello, order 1043 should be three Fernleaf Green, but one of the "
            "tins is Morning Mist. It's unopened. Can I swap it?\n\nLee\n"
        ),
        "inbox/2026-10-26-wholesale.md": (
            "From: Ada Brook <ada.brook@example.com>\nTo: hello@fernleaf.example\nDate: 26 Oct 2026\n"
            "Subject: Tea for our café\n\nHi! I run a small café and love your Quiet Chamomile. Do you sell it in "
            "larger bags, and is there a price for ordering 10 at a time?\n\nBest,\nAda\n"
        ),
    },
    "charity": {
        "README.md": "# Riverside Food Bank\n\nA made-up charity's documents for trying Row-Bot.\n",
        "annual-report-draft.md": (
            "# Riverside Food Bank: annual report (draft)\n\nThis year 412 volunteers gave 9,800 hours. We "
            "handed out 31,000 food parcels, up 12% on last year.\n\n## Still to write\n\n- Thanks to funders\n"
            "- Plans for the new warehouse\n"
        ),
        "donations-2026.csv": (
            "month,donors,amount\nJanuary,140,4210\nFebruary,128,3980\nMarch,151,4630\nApril,133,4100\n"
            "May,160,5020\nJune,149,4710\n"
        ),
        "volunteer-rota.md": (
            "# Volunteer rota, November\n\n| Day | Morning | Afternoon |\n| --- | --- | --- |\n"
            "| Monday | Priya, Tom | Aisha |\n| Wednesday | Ben | Priya, Joe |\n| Saturday | Everyone | — |\n"
        ),
    },
    "paperwork": {
        "README.md": "# Household paperwork\n\nMade-up bills and letters for trying Row-Bot.\n",
        "energy-bill-october.md": (
            "# Brightside Energy: October bill\n\nAccount 0000-1111 (made up)\n\nElectricity: 214 kWh, £61.20\n"
            "Gas: 402 kWh, £38.90\nTotal due 1 November: £100.10\n"
        ),
        "car-insurance-renewal.md": (
            "# Renewal notice\n\nYour car insurance with Oakfield Cover renews on 3 December at £486.00 a "
            "year (last year £432.00). Call or write before then to change or cancel.\n"
        ),
        "council-tax-2026.md": "# Council tax 2026–27\n\nBand C, £1,872.40 a year, 10 monthly payments of £187.24.\n",
    },
    "code": {
        "README.md": "# tiny-todo\n\nA made-up command-line to-do list for trying Row-Bot's Developer tools.\n",
        "todo.py": (
            "\"\"\"A tiny to-do list kept in a JSON file.\"\"\"\n\nimport json\nimport sys\nfrom pathlib import Path\n\n"
            "STORE = Path('todos.json')\n\n\ndef load():\n    return json.loads(STORE.read_text()) if STORE.exists() "
            "else []\n\n\ndef add(text):\n    items = load()\n    items.append({'text': text, 'done': False})\n"
            "    STORE.write_text(json.dumps(items))\n    return len(items)\n\n\ndef done(number):\n"
            "    items = load()\n    items[number - 1]['done'] = True\n    STORE.write_text(json.dumps(items))\n\n\n"
            "if __name__ == '__main__':\n    command, *rest = sys.argv[1:] or ['list']\n    if command == 'add':\n"
            "        print(add(' '.join(rest)))\n    elif command == 'done':\n        done(int(rest[0]))\n    else:\n"
            "        for index, item in enumerate(load(), 1):\n"
            "            print(index, '[x]' if item['done'] else '[ ]', item['text'])\n"
        ),
        "test_todo.py": (
            "import todo\n\n\ndef test_add_then_done(tmp_path, monkeypatch):\n"
            "    monkeypatch.setattr(todo, 'STORE', tmp_path / 'todos.json')\n"
            "    assert todo.add('buy milk') == 1\n    todo.done(1)\n    assert todo.load()[0]['done'] is True\n"
        ),
    },
}
