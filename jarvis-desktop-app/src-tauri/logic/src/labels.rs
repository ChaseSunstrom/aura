//! The tray's status line: the agent's state in the reactor's own word, from the
//! generated table (`states.rs`, from `tests/contracts/reactor.json`), so the tray
//! says what the console, the phone and a chat message say.

use crate::states::STATES;

/// The agent's state as one of the reactor's eleven: `starting` is a cold start
/// (warming), and a word the table does not know is idle rather than an error.
pub fn reactor_state(state: &str) -> &'static str {
    if state == "starting" {
        return "warming";
    }
    STATES.iter().find(|(name, ..)| *name == state).map(|(name, ..)| *name).unwrap_or("idle")
}

pub fn word(state: &str) -> &'static str {
    let resolved = reactor_state(state);
    STATES.iter().find(|(name, ..)| *name == resolved).map(|(_, _, w)| *w).unwrap_or("Ready")
}

pub fn glyph(state: &str) -> &'static str {
    let resolved = reactor_state(state);
    STATES.iter().find(|(name, ..)| *name == resolved).map(|(_, g, _)| *g).unwrap_or("")
}

/// `Needs you — approve a file write`; an offline agent says why in plain words
/// rather than calling a missing agent an error.
pub fn status_label(state: &str, detail: &str) -> String {
    let resolved = reactor_state(state);
    let said = if !detail.is_empty() {
        detail
    } else if resolved == "offline" {
        "the agent is not running"
    } else {
        ""
    };
    if said.is_empty() {
        word(resolved).to_string()
    } else {
        format!("{} — {said}", word(resolved))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_the_state_in_the_reactors_own_word() {
        assert_eq!(status_label("idle", ""), "Ready");
        assert_eq!(status_label("attention", "approve a file write"), "Needs you — approve a file write");
        assert_eq!(status_label("tool", ""), "Working");
    }

    #[test]
    fn a_missing_agent_is_not_an_error_and_a_cold_start_is_warming() {
        assert_eq!(status_label("offline", ""), "Offline — the agent is not running");
        assert_eq!(reactor_state("starting"), "warming");
        assert_eq!(reactor_state("somewhere new"), "idle");
    }

    #[test]
    fn every_state_has_a_word_and_a_glyph() {
        assert_eq!(STATES.len(), 11);
        for (name, g, w) in STATES {
            assert!(!g.is_empty() && !w.is_empty(), "{name}");
            assert_eq!(glyph(name), *g);
        }
    }
}
