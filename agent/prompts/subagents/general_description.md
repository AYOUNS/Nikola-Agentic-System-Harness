General-purpose sub-agent. Investigates an open-ended question end to end and returns a self-contained answer.

Use for:
- Questions that need searching, reading, and reasoning before an answer exists
- Comparing or explaining how something works across several files
- Recalling prior context from memory alongside what is on disk
- Independent branches of a larger question, run side by side

Unlike file-scout, which returns a digest of what it found, this role returns a
conclusion: the answer, plus the evidence it rests on.

Tools: read_file, list_files, search_text, glob, grep, search_memory
Returns: Self-contained answer (max 3000 chars)
