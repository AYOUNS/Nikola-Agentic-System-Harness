# Use Case: File Search

This use case shows the agent searching, reading, and summarising files inside the workspace
using the built-in filesystem tools. It is also the canonical `file-scout` sub-agent task.

---

## Example prompts

**Search for a pattern:**

```
Search all files in the workspace for the word "TODO" and list the matching lines.
```

**Find files by name:**

```
Find every Markdown file in .nikola/skills.
```

**Read and summarise:**

```
Read all the rule files in .nikola/rules and give me a one-sentence summary of each.
```

**Delegate to a file-scout sub-agent:**

```
Use a file-scout sub-agent to find every file in .nikola/skills that references
an external HTTP API, and return a brief digest.
```

---

## Tools involved

| Tool | Use |
|---|---|
| `glob` | Find files by name pattern (e.g. `**/*.md`) |
| `grep` | Regex search across file contents |
| `search_text` | Full-text search across the workspace |
| `list_files` | Recursive directory listing |
| `read_file` | Read a specific file's content |

All five are `allow` by default — no confirmation required.

---

## Canonical E2E test case: find-and-inspect

This is one of the two spec §8.2 canonical cases.

**Pre-populate the workspace:**

```python
# In your pytest fixture:
(workspace / "pathABC" / "data").mkdir(parents=True)
(workspace / "pathABC" / "data" / "notes.txt").write_text("This\nfile\nhas\none\nline")
(workspace / "readme.md").write_text("# Readme")
(workspace / "empty").mkdir()
```

**Prompt:**

```
List the files in pathABC and tell me how many lines notes.txt has.
```

**Assertions:**

- Agent calls `list_files` on `pathABC`
- Agent calls `read_file` on `pathABC/data/notes.txt`
- Final answer reports exactly **5 lines**

---

## Tips

- Sub-agents read `/workspace`, not the repository source. Target `.nikola/skills`,
  `.nikola/rules`, or files created earlier in the session.
- `search_text` is full-text but not regex; use `grep` when you need pattern matching.
- `glob` is faster than `list_files` when you know the extension you're looking for.
