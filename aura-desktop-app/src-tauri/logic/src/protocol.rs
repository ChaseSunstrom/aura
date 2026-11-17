//! The app's side of the agent's loopback socket (`aura_desktop/ipc.py`) and of
//! the one line the agent announces it on (`ipc.announce`).
//!
//! One line of JSON per frame. The agent sends `status`, `ask`, `overlay` and
//! `handoff`; the app sends `answer`, `takeover`, `resume` and `dictate`. Whether an action may run at all was decided by the agent long
//! before the app is involved: this only parses, and what a consent prompt looks
//! like is the consent page's job.

use std::path::PathBuf;

use serde_json::Value;

/// A frame from the agent.
#[derive(Debug, Clone, PartialEq)]
pub enum Frame {
    Status { state: String, detail: String },
    /// A held action waiting for a person: `id`, `description`, `params`, `tier`,
    /// `reason`, `allow_always`. Kept whole, because the consent page shows it.
    Ask(Value),
    /// One step of the visible path (T-0617): `op` is path, highlight, ripple,
    /// caret, scroll, key or hide. Kept whole: the overlay page draws it and
    /// checks the numbers; the app only needs `op` to show or hide the window.
    Overlay(Value),
    /// "Show it on my desktop": open this console session here. The id is
    /// checked here too, because it goes into the console's URL.
    Handoff { session: String },
}

/// `tests/contracts/handoff-session.json`: letters, digits and `._:-`, 1 to
/// 128 of them -- an id, never an address or a second query parameter.
pub fn session_id_ok(session: &str) -> bool {
    (1..=128).contains(&session.len())
        && session.bytes().all(|b| b.is_ascii_alphanumeric() || b"_.:-".contains(&b))
}

/// One line off the socket; `None` for a half-written line or something that is
/// not the agent.
pub fn parse_frame(line: &str) -> Option<Frame> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    match value.get("type")?.as_str()? {
        "status" => Some(Frame::Status {
            state: value.get("state").and_then(Value::as_str).unwrap_or("idle").to_string(),
            detail: value.get("detail").and_then(Value::as_str).unwrap_or_default().to_string(),
        }),
        "ask" if value.get("id").and_then(Value::as_str).is_some() => Some(Frame::Ask(value)),
        "overlay" if value.get("op").and_then(Value::as_str).is_some() => Some(Frame::Overlay(value)),
        "handoff" => value
            .get("session")
            .and_then(Value::as_str)
            .filter(|s| session_id_ok(s))
            .map(|s| Frame::Handoff { session: s.to_string() }),
        _ => None,
    }
}

/// The first line the app writes: the token, on its own. The agent counts the
/// connection only once it has seen it, so an app that has not authenticated is
/// never "the shell" and a question is reported unattended rather than held.
pub fn hello(token: &str) -> String {
    serde_json::json!({ "token": token }).to_string() + "\n"
}

/// The three answers the agent accepts; anything else is refused before it is sent.
pub fn answer(id: &str, verdict: &str) -> Option<String> {
    matches!(verdict, "approved" | "approved_always" | "denied")
        .then(|| serde_json::json!({ "type": "answer", "id": id, "verdict": verdict }).to_string() + "\n")
}

/// The owner pressed the global Esc while the agent was working: stop now.
pub fn takeover() -> String {
    serde_json::json!({ "type": "takeover" }).to_string() + "\n"
}

/// The tray's "Resume Jarvis": the owner's word to carry on. Nothing else
/// resumes a take-over.
pub fn resume() -> String {
    serde_json::json!({ "type": "resume" }).to_string() + "\n"
}

/// The words the owner dictated by holding the dictation key (T-1258), for the agent to type into
/// the focused window. Only the app's key handler makes this frame, and it is the owner's key press,
/// not the model, that authorises it (`aura_desktop/dictation.py`).
pub fn dictate(text: &str) -> String {
    serde_json::json!({ "type": "dictate", "text": text }).to_string() + "\n"
}

/// The agent's `{"jarvis_shell": {"port": …, "token_file": …}}` line, if this is it.
pub fn parse_announcement(line: &str) -> Option<(u16, PathBuf)> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    let shell = value.get("jarvis_shell")?;
    let port = u16::try_from(shell.get("port")?.as_u64()?).ok().filter(|p| *p > 0)?;
    let token_file = PathBuf::from(shell.get("token_file")?.as_str()?);
    Some((port, token_file))
}

/// The subcommands the app's pages may run the agent with. `run` and `pair` are
/// the app's own to start, `console` and `enrol` want a terminal, and anything
/// the list does not name is refused rather than passed to a binary that can
/// change the machine's policy. A config path is refused too: the pages act on
/// this agent's config, never on one a page names.
pub const CLI_ALLOWED: &[&str] = &["tiers", "policy", "audit", "doctor", "status"];

pub fn cli_allowed(args: &[String]) -> bool {
    match args.first() {
        Some(first) => {
            CLI_ALLOWED.contains(&first.as_str()) && !args.iter().any(|a| a == "-c" || a.starts_with("--config"))
        }
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_status_and_ask_and_ignores_the_rest() {
        assert_eq!(
            parse_frame(r#"{"type":"status","state":"tool","detail":"read_file"}"#),
            Some(Frame::Status { state: "tool".into(), detail: "read_file".into() })
        );
        assert_eq!(parse_frame(r#"{"type":"status"}"#), Some(Frame::Status { state: "idle".into(), detail: String::new() }));
        let ask = parse_frame(r#"{"type":"ask","id":"a1","description":"Write a file","tier":2}"#);
        assert!(matches!(ask, Some(Frame::Ask(v)) if v["id"] == "a1"));
        for line in ["", "{\"type\":\"sta", "not json", r#"{"type":"ask"}"#, r#"{"type":"other"}"#, "[]"] {
            assert_eq!(parse_frame(line), None, "{line:?}");
        }
    }

    #[test]
    fn reads_overlay_steps_and_only_a_well_formed_handoff() {
        let step = parse_frame(r#"{"type":"overlay","op":"ripple","at":[3,4]}"#);
        assert!(matches!(step, Some(Frame::Overlay(v)) if v["op"] == "ripple"));
        assert_eq!(parse_frame(r#"{"type":"overlay"}"#), None);
        let contract: Value =
            serde_json::from_str(include_str!("../../../../tests/contracts/handoff-session.json")).unwrap();
        for good in contract["valid"].as_array().unwrap() {
            let line = serde_json::json!({ "type": "handoff", "session": good }).to_string();
            assert_eq!(parse_frame(&line), Some(Frame::Handoff { session: good.as_str().unwrap().into() }), "{line}");
        }
        for bad in contract["invalid"].as_array().unwrap() {
            let line = serde_json::json!({ "type": "handoff", "session": bad }).to_string();
            assert_eq!(parse_frame(&line), None, "{line}");
        }
        assert_eq!(takeover(), "{\"type\":\"takeover\"}\n");
        assert_eq!(resume(), "{\"type\":\"resume\"}\n");
        assert_eq!(dictate("hi \"there\"\n"), "{\"text\":\"hi \\\"there\\\"\\n\",\"type\":\"dictate\"}\n");
    }

    #[test]
    fn writes_the_token_first_and_only_the_three_verdicts() {
        assert_eq!(hello("t0k"), "{\"token\":\"t0k\"}\n");
        let line = answer("a1", "approved_always").unwrap();
        let value: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(value["type"], "answer");
        assert_eq!(value["verdict"], "approved_always");
        assert!(line.ends_with('\n'));
        assert_eq!(answer("a1", "yes please"), None);
    }

    #[test]
    fn reads_the_agents_announcement() {
        let line = r#"{"jarvis_shell": {"port": 4242, "token_file": "/home/me/.local/state/jarvis-desktop/shell-token"}}"#;
        assert_eq!(
            parse_announcement(line),
            Some((4242, PathBuf::from("/home/me/.local/state/jarvis-desktop/shell-token")))
        );
        for line in [
            "INFO aura_desktop: connected",
            r#"{"jarvis_shell": {"port": 0, "token_file": "x"}}"#,
            r#"{"jarvis_shell": {"port": 70000, "token_file": "x"}}"#,
            r#"{"jarvis_shell": {"port": 1}}"#,
        ] {
            assert_eq!(parse_announcement(line), None, "{line}");
        }
    }

    #[test]
    fn the_pages_may_read_and_set_policy_but_not_start_or_repoint_the_agent() {
        let args = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(cli_allowed(&args(&["tiers", "--json"])));
        assert!(cli_allowed(&args(&["policy", "set", "write_file", "never"])));
        assert!(cli_allowed(&args(&["audit", "--json", "--limit", "50"])));
        for bad in [
            &["run"][..],
            &["pair", "jarvis://pair?x"],
            &["console"],
            &["enrol"],
            &[],
            &["tiers", "-c", "/tmp/other.json"],
            &["doctor", "--config=/x"],
        ] {
            assert!(!cli_allowed(&args(bad)), "{bad:?}");
        }
    }
}
