# Vulture allow-list — interface-shaped false positives only.
#
# Passed to `vulture` as an extra scan target; each name written here is treated as "used" so a
# genuine dead-symbol regression still fails the gate. Every entry is a symbol required by an
# external contract (a caller keyword, an overridden protocol, a pytest fixture) that vulture cannot
# see is exercised. Nothing here silences dead code — dead code is deleted, not listed.
#
# One line per name, with the reason. Keep this list short; prefer renaming a truly-unused local to
# an underscore-prefixed name (vulture ignores those) over adding an entry here.

# hermes/plugins/_plugin_loader.py — FakeCtx mirrors Hermes' PluginContext signatures so plugin
# tests exercise the real call shape. `args_hint` is a register_command keyword no plugin passes yet;
# dropping it would make the fake reject a valid Hermes call.
args_hint

# hermes/plugins/comfyui-imagegen/__init__.py — the provider's generate() overrides Hermes'
# ImageGenProvider ABC, whose keyword contract includes image_url / reference_image_urls. This
# provider declares max_reference_images: 0 and ignores them, but the parameters must stay to satisfy
# the protocol Hermes calls by keyword.
image_url
reference_image_urls
