"""``row-bot run``: one task, run headless to its end, then the process exits.

For harnesses and benchmarks that install Row-Bot into a plain task container, hand it a task and read back
what it did (Harbor's Terminal-Bench and DeepSWE, the Hugging Face Harness Leaderboard). The task runs as one
conversation with Row-Bot's normal prompts and tools, on a fresh throwaway data folder unless ``--data-dir``
names one; nothing else starts: no server, tray, window, update check or install-count ping.

- ``cli``: the subcommand's flags and the process set-up that must come before anything else is imported;
- ``run``: the run itself (the model, the conversation, the answers to approvals nobody can give, the files);
- ``atif``: the conversation as an ATIF trajectory (Harbor's Agent Trajectory Interchange Format).
"""
