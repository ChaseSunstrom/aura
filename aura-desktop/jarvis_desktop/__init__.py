"""The desktop agent's old module name, kept so ``python -m jarvis_desktop`` still starts it.

The package is ``aura_desktop`` since T-0960. A PC set up before then starts the agent by the old
name (the README's Windows task ran ``pythonw -m jarvis_desktop run``), and the agent updates
itself unattended: without this package the next logon after an update fails with "No module
named jarvis_desktop" and the agent stays down with nobody told (T-1435).

Does not cover: ``import jarvis_desktop.<module>``. Only the entry point is kept; import
``aura_desktop``.
"""
