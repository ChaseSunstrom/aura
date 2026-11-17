"""Tool schemas for the ``files_share`` toolset.

Each description says whether the tool changes the share, because the model
picks from these strings alone and would otherwise try a write expecting it to
be free.
"""
from __future__ import annotations

from . import tools

_LIMIT = f"{tools.MAX_BYTES} bytes (256 KB)"

FILESHARE_LIST = {
    "name": "fileshare_list",
    "description": ("My files, what's on the share, what is in a folder: list a directory on "
                    "the DAV file share. `path` is relative to the share "
                    "root; omit it for the root itself. Read-only, no approval. Returns path, "
                    "name, is_dir, size, content_type and modified for each entry."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string",
                                "description": "Directory relative to the share root."}},
        "required": [],
    },
}

FILESHARE_READ = {
    "name": "fileshare_read",
    "description": ("Open a file from my files, what does that document say: read one text "
                    "file from the DAV file share. Read-only, no approval. "
                    f"Refuses binary files and anything over {_LIMIT}, naming the limit — "
                    "check the size with fileshare_list first for a large file."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string",
                                "description": "File relative to the share root."}},
        "required": ["path"],
    },
}

FILESHARE_WRITE = {
    "name": "fileshare_write",
    "description": ("Save this to my files, put a file on the share: write a text file to the "
                    "DAV file share, replacing whatever is at that "
                    "path. Changes the share, so it needs approval. Text only, and at most "
                    f"{_LIMIT}."),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File relative to the share root."},
            "content": {"type": "string", "description": "The full new contents (UTF-8 text)."},
        },
        "required": ["path", "content"],
    },
}

FILESHARE_DELETE = {
    "name": "fileshare_delete",
    "description": ("Delete a file or directory from the DAV file share. Changes the share and "
                    "cannot be undone, so it needs approval. Deleting a directory takes "
                    "everything under it."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string",
                                "description": "File or directory relative to the share root."}},
        "required": ["path"],
    },
}

FILES_SEARCH = {
    "name": "files_search",
    "description": ("Which of my files says something about X, find the document about X when the "
                    "name is not known: search the text files on the DAV file share by meaning. "
                    "Read-only, no approval. Returns up to five passages, each with its file's path; "
                    "open the file with fileshare_read. Covers text files only (no PDFs or office "
                    f"files, nothing over {_LIMIT}); `indexed` of `files` says how much of the share "
                    "is searchable yet, and a search right after many files changed indexes a few more."),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string",
                                 "description": "What to look for, in plain words."}},
        "required": ["query"],
    },
}
