PROXMOX_NODES = {
    "name": "proxmox_nodes",
    "description": "Which Proxmox nodes/hosts are in the cluster and how busy each is: status, CPU %, memory used/total, uptime. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PROXMOX_GUESTS = {
    "name": "proxmox_guests",
    "description": ("Every LXC container and VM across the cluster: vmid, name, type (lxc|qemu), node, status "
                    "(running|stopped), CPU %, memory, uptime. Read-only. Use it to find a guest's vmid before acting on it."),
    "parameters": {
        "type": "object",
        "properties": {"node": {"type": "string", "description": "Restrict to one node (optional)"}},
        "required": [],
    },
}

PROXMOX_GUEST_STATUS = {
    "name": "proxmox_guest_status",
    "description": "Detailed status of one guest (by vmid): state, uptime, CPU, memory, disk, network counters. Read-only.",
    "parameters": {"type": "object", "properties": {"vmid": {"type": "integer"}}, "required": ["vmid"]},
}

PROXMOX_GUEST_ACTION = {
    "name": "proxmox_guest_action",
    "description": ("Start, stop (hard), shutdown (graceful), reboot or suspend/resume an LXC or VM by vmid. Stopping "
                    "or rebooting a guest interrupts whatever runs on it (the JARVIS host, GitLab, n8n…), so it needs approval. "
                    "Returns the Proxmox task id and its result."),
    "parameters": {
        "type": "object",
        "properties": {
            "vmid": {"type": "integer"},
            "action": {"type": "string", "enum": ["start", "stop", "shutdown", "reboot", "suspend", "resume"]},
        },
        "required": ["vmid", "action"],
    },
}

DESTRUCTIVE = {"proxmox_guest_action"}
