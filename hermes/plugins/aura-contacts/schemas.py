"""Tool schemas for the ``contacts`` toolset. Both tools are read-only."""
from __future__ import annotations

CONTACTS_SEARCH = {
    "name": "contacts_search",
    "description": ("X's phone number or email, who is X, find a contact: search the address "
                    "books on the DAV server by substring of a name, email "
                    "address or phone number. Read-only, no approval. Returns fn, emails, tels, "
                    "uid and the book each contact is in. There is no contact-writing tool."),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Name, email or number fragment."},
            "limit": {"type": "integer", "description": "Rows to return; default 20."},
        },
        "required": ["query"],
    },
}

CONTACTS_GET = {
    "name": "contacts_get",
    "description": ("One contact, by uid or by exact full name (case-insensitive). Read-only, "
                    "no approval. On a miss it returns near-matching names, so prefer "
                    "contacts_search when the spelling is uncertain."),
    "parameters": {
        "type": "object",
        "properties": {
            "uid": {"type": "string",
                    "description": "The contact's UID, as a search row gives it."},
            "name": {"type": "string", "description": "Exact full name (FN)."},
        },
        "required": [],
    },
}
