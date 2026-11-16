---
title: datacard-lint
emoji: 📋
colorFrom: yellow
colorTo: gray
sdk: static
pinned: false
license: mit
short_description: Check a dataset card's content in your browser
---

# datacard-lint

Check a Hugging Face dataset card's content: the template's `[More Information Needed]` left in, empty sections, files
it names that are not there, and data files that do not match it. It runs in your browser (Pyodide) and only reads from
huggingface.co.

Open a dataset by id, or paste a card. `?dataset=owner/name` in the address checks one straight away.

The same checks as a command and a GitHub Action: https://github.com/HivemindOS-Agents/datacard-lint
