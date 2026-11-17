//! The second global key (T-0696): from any window, show the island with its
//! typing line focused. The first is push-to-talk (`Super+Space`); this one is
//! for the person who would rather type. Pure, like the rest of this crate: the
//! app crate registers the key and raises the popup, this says which key, what
//! the page is told, and what the tray says when the key is not ours.

/// Not a bare Ctrl+K: registered globally that would take the chord from every
/// browser and editor, where it already means "search" or "link".
pub const DEFAULT_PALETTE_KEY: &str = "Ctrl+Shift+K";

/// What the popup page listens for (`popupMood.ts` `SUMMON_EVENT`); `to` says why it was shown.
/// Run in the page: the popup is the console's own page and has no bridge into the app.
pub const SUMMON_TO_TYPE_SCRIPT: &str =
    "window.dispatchEvent(new CustomEvent('jarvis:summon', { detail: { to: 'type' } }))";

/// The accelerator to register: the setting's value, or the default when it is unset or blank.
pub fn palette_key(setting: Option<&str>) -> String {
    match setting.map(str::trim) {
        Some(key) if !key.is_empty() => key.to_string(),
        _ => DEFAULT_PALETTE_KEY.to_string(),
    }
}

/// The two keys must differ: a shortcut registers once, and the handler would send the press
/// to the wrong place. The app compares the parsed `Shortcut`s (case and modifier order do not
/// matter there); this only words the refusal, for the log and the tray.
pub fn palette_clash(key: &str) -> String {
    format!("{key} is also the push-to-talk key")
}

/// The tray's line for the key: what it does and which chord, or, when another app owns the
/// chord (or it is not a key this app can register), that it is not available and why.
pub fn palette_tray_label(name: &str, key: &str, refusal: Option<&str>) -> String {
    match refusal {
        None => format!("Type to {name} ({key})"),
        Some(why) => format!("Type to {name}: {key} is not available ({why})"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_palette_key_is_the_setting_or_ctrl_shift_k() {
        assert_eq!(palette_key(None), "Ctrl+Shift+K");
        assert_eq!(palette_key(Some("")), "Ctrl+Shift+K");
        assert_eq!(palette_key(Some("   ")), "Ctrl+Shift+K");
        assert_eq!(palette_key(Some(" Alt+Space ")), "Alt+Space");
    }

    #[test]
    fn the_palette_key_is_never_the_bare_ctrl_k_browsers_and_editors_own() {
        assert_ne!(DEFAULT_PALETTE_KEY, "Ctrl+K");
    }

    #[test]
    fn the_palette_summon_tells_the_page_to_type() {
        assert!(SUMMON_TO_TYPE_SCRIPT.contains("'jarvis:summon'"));
        assert!(SUMMON_TO_TYPE_SCRIPT.contains("to: 'type'"));
    }

    #[test]
    fn the_palette_tray_line_names_the_key_or_says_why_it_is_not_ours() {
        assert_eq!(palette_tray_label("Jarvis", "Ctrl+Shift+K", None), "Type to Jarvis (Ctrl+Shift+K)");
        let refused = palette_tray_label("AURA", "Ctrl+Shift+K", Some("another app holds it"));
        assert!(refused.contains("not available") && refused.contains("another app holds it"));
    }

    #[test]
    fn the_palette_clash_line_names_the_key() {
        assert!(palette_clash("Super+Space").contains("Super+Space"));
    }
}
