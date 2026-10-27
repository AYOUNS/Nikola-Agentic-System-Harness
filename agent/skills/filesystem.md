---
name: filesystem
description: Guide to using built-in filesystem tools
version: 1.0
---

# Filesystem Operations Guide

This agent provides several built-in filesystem tools for working with files
and directories within the sandboxed workspace.

## Available Tools

### File Operations

- **`create_file`** - Create a new file with content
  - Parameters: `path` (string), `content` (string)
  - Example: `create_file(path="notes.txt", content="Hello World")`

- **`read_file`** - Read contents of a file
  - Parameters: `path` (string)
  - Returns: File contents as string

- **`modify_file`** - Update an existing file
  - Parameters: `path` (string), `content` (string)
  - Note: Overwrites entire file content

- **`search_text`** - Search for text within files
  - Parameters: `pattern` (string), `path` (string, optional)
  - Returns: List of matches with file paths and line numbers

### Directory Operations

- **`create_folder`** - Create a new directory
  - Parameters: `path` (string)
  - Example: `create_folder(path="output")`

- **`list_files`** - List contents of a directory
  - Parameters: `path` (string, optional, defaults to current directory)
  - Returns: List of files and directories

- **`navigate_folder`** - Change current working directory
  - Parameters: `path` (string)

## Security Model

All filesystem operations are **restricted to the workspace root** (`/workspace`).

**Blocked operations:**
- Paths outside workspace (e.g., `../`, absolute paths)
- **Delete operations** (file or folder deletion is NOT available by design)

## Best Practices

1. **Use relative paths** - All paths are relative to `/workspace`
2. **Create output directories first** - Use `create_folder` before writing files
3. **Check directory contents** - Use `list_files` to verify structure
4. **Search before reading** - Use `search_text` to find relevant files
5. **Organize by task** - Create separate folders for different tasks

## Common Patterns

### Create a project structure
```
1. create_folder(path="project")
2. create_folder(path="project/src")
3. create_folder(path="project/output")
4. create_file(path="project/README.md", content="# Project")
```

### Find and update files
```
1. search_text(pattern="TODO", path=".")
2. read_file(path="found/file.txt")
3. modify_file(path="found/file.txt", content="Updated content")
```

### Generate reports
```
1. list_files(path="data")
2. read_file(path="data/input.csv")
3. create_folder(path="reports")
4. create_file(path="reports/summary.md", content="# Summary\n...")
```

## Troubleshooting

**"Path outside workspace" error:**
- Check for `../` or absolute paths
- All paths must be within `/workspace`

**"File not found" error:**
- Verify path with `list_files`
- Check for typos in filename
- Ensure parent directory exists

**"Permission denied" error:**
- Container may not have write permissions
- Check workspace volume mount in `compose.yaml`
