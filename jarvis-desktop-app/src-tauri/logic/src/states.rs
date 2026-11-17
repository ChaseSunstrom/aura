// @generated from design/tokens.json and tests/contracts/reactor.json — DO NOT EDIT. Run `python3 design/build.py`.
//! The reactor's eleven states as the desktop app carries them: the static glyph
//! (the reduced-motion column, GLASS_HUD §10) and the state's word.

/// `(state, glyph, word)`, highest precedence first, `goal` (the overlay) last.
pub const STATES: &[(&str, &str, &str)] = &[
    ("kill", "◯", "Kill switch"),
    ("offline", "◌", "Offline"),
    ("alert", "⊖", "Alert"),
    ("attention", "◡", "Needs you"),
    ("warming", "○", "Warming up"),
    ("speaking", "◎", "Speaking"),
    ("listening", "●", "Listening"),
    ("tool", "◔", "Working"),
    ("thinking", "◐", "Thinking"),
    ("idle", "○", "Ready"),
    ("goal", "⊙", "On a goal"),
];

/// `motion.dur.float-out`: how long the popup's page plays its exit before the window hides.
pub const FLOAT_OUT_MS: u64 = 220;
/// `motion.dur.glide`: a floating window moving to a new place on the screen.
pub const GLIDE_MS: u64 = 360;

/// The popup window round the island's pill: `material.floating.pill-*` and the
/// `material.floating.halo` margin on every side.
pub const ISLAND_PILL_WINDOW: (f64, f64) = (268.0, 100.0);
/// `spring.glass.settle`: the island's morph, waited out before the window shrinks.
pub const ISLAND_SETTLE_MS: u64 = 380;
