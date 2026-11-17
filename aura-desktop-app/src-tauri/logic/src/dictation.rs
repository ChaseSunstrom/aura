//! Hold-to-dictate (T-1258): a held global key records, and what the adapter heard is typed into
//! whatever window has the focus. Pure, like the rest of this crate: the app crate registers the
//! key, tells the popup page to record and sends the agent the words; this says when a press is
//! armed, which answer from the page is accepted, and what the page is told.
//!
//! The page is the console's own (`/?popup=1`) and has no bridge into the app, so its answer comes
//! back in its document title (`tests/contracts/dictation.json`). A title is anything the page
//! chooses to write, which is why the answer is taken only when it is the one this press armed,
//! only after the key was let go, and only once.

use std::path::PathBuf;
use std::time::Duration;

use url::{Host, Url};

/// A key held longer than this ends as if it had been released: a stuck key, or a hand off the
/// keyboard, must not record for ever.
pub const MAX_HOLD: Duration = Duration::from_secs(60);

/// After the release, how long the page has to record's tail, send it to the adapter and answer.
/// Past it the press is forgotten, and a late answer is ignored.
pub const RESULT_WINDOW: Duration = Duration::from_secs(30);

/// More than this is not a sentence someone dictated; the agent types nothing over it either
/// (`aura_desktop/actions/inputauto.py` `MAX_TYPE_CHARS`).
pub const MAX_TEXT_CHARACTERS: usize = 4000;

/// What opens the title segment the page answers in.
pub const RESULT_PREFIX: &str = "typed:";

/// The accelerator is only the owner's if it cannot be mistaken for typing: a global key with no
/// modifier would take that letter from every window. Each token but the last must be there.
pub fn needs_modifier(key: &str) -> bool {
    let mut tokens = key.split('+').map(str::trim);
    tokens.next_back().is_some_and(|last| !last.is_empty()) && tokens.any(|t| !t.is_empty())
}

/// The refusal for a dictation chord that is also another key of this app's, for the log and the
/// tray.
pub fn dictation_clash(key: &str, other: &str) -> String {
    format!("{key} is also the {other} key")
}

/// The tray's line for the key, or why the chord is not ours.
pub fn dictation_tray_label(name: &str, key: &str, refusal: Option<&str>) -> String {
    match refusal {
        None => format!("Hold {key} to dictate into the focused window ({name})"),
        Some(why) => format!("Dictation: {key} is not available ({why})"),
    }
}

/// Where the bundled agent reads its config: the app starts it with no `--config`, so this is
/// `default_config_path` in `aura_desktop/config.py`, and `tests/contracts/input-automation-switch.json`
/// holds the two together. `None` when there is no home to look under.
pub fn agent_config_path(os: &str, appdata: Option<&str>, xdg_config_home: Option<&str>, home: Option<&str>) -> Option<PathBuf> {
    fn set(value: Option<&str>) -> Option<&str> {
        value.filter(|v| !v.is_empty())
    }
    let base = match os {
        "windows" => match set(appdata) {
            Some(dir) => PathBuf::from(dir),
            None => PathBuf::from(set(home)?).join("AppData").join("Roaming"),
        },
        "macos" => PathBuf::from(set(home)?).join("Library").join("Application Support"),
        _ => match set(xdg_config_home) {
            Some(dir) => PathBuf::from(dir),
            None => PathBuf::from(set(home)?).join(".config"),
        },
    };
    Some(base.join("jarvis-desktop").join("config.json"))
}

/// The agent's `input_automation.enabled`, read as `load_config` reads it: `AURA_INPUT_ENABLED`
/// when set at all (an empty one included) beats the file, a word counts as on only when the
/// agent's `_as_bool` says so, and anything else is off. The file is the agent's default
/// (off), so a missing, unreadable or malformed one is off here too: the key fails closed.
pub fn input_automation_on(env: Option<&str>, config_text: Option<&str>) -> bool {
    let word_is_on = |word: &str| matches!(word.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    if let Some(word) = env {
        return word_is_on(word);
    }
    let value = config_text.and_then(|text| serde_json::from_str::<serde_json::Value>(text).ok());
    match value.as_ref().and_then(|v| v.get("input_automation")?.get("enabled")) {
        Some(serde_json::Value::Bool(on)) => *on,
        Some(serde_json::Value::String(word)) => word_is_on(word),
        _ => false,
    }
}

/// Why the dictation key must not be registered while the agent would drop what it is given: its
/// typing switch is off (or not known to be on). Said in the tray and the log.
pub fn typing_refusal(env: Option<&str>, config_text: Option<&str>) -> Option<String> {
    (!input_automation_on(env, config_text))
        .then(|| "typing is switched off in the desktop agent (input_automation.enabled in its config); turn it on and restart this app".into())
}

/// What the page is told, run in it. `id` is minted here from digits and letters only (a test
/// holds it), so it can sit in the script as a string without a quote of its own.
pub fn arm_script(down: bool, id: &str) -> String {
    format!("window.dispatchEvent(new CustomEvent('jarvis:dictate', {{ detail: {{ down: {down}, id: '{id}' }} }}))")
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
enum State {
    #[default]
    Idle,
    /// The key is down and the page was told to record.
    Held(String),
    /// The key came up; the page's answer is awaited.
    Waiting(String),
}

/// What a title carried, for the armed press.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Heard {
    /// Not an answer for the press being waited on: the title is somebody else's business.
    Ignore,
    /// The press ended with nothing to type: a tap, silence, a refused voice, a failed transcription.
    Nothing,
    /// The words to type.
    Text(String),
}

/// What a timer found when it fired for a press.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Expired {
    /// The key was still down: it is let go now, and the page must be told.
    Released,
    /// The answer never came: the press is forgotten.
    Abandoned,
    /// That press was already over; a stale timer does nothing.
    Stale,
}

/// What the page is told once the app has the words, run in it: the segment leaves the title at
/// once (the page's own timer is only the fall-back).
pub fn ack_script(id: &str) -> String {
    format!("window.dispatchEvent(new CustomEvent('jarvis:dictated', {{ detail: {{ id: '{id}' }} }}))")
}

/// The words are sent to the adapter to be heard, and the page that does it is the console's own:
/// so the console must be https (the words cross the network inside it) or on this machine.
pub fn console_allows(url: &str) -> Result<(), String> {
    let why = || "the console must be https or loopback, so what is said never crosses the network in the clear".to_string();
    let Ok(parsed) = Url::parse(url) else { return Err(why()) };
    match (parsed.scheme(), parsed.host()) {
        ("https", Some(_)) => Ok(()),
        ("http", Some(Host::Domain("localhost"))) => Ok(()),
        ("http", Some(Host::Ipv4(ip))) if ip.is_loopback() => Ok(()),
        ("http", Some(Host::Ipv6(ip))) if ip.is_loopback() => Ok(()),
        _ => Err(why()),
    }
}

/// Why a press must not record, when it must not: nothing to record with, a console the words may
/// not cross to, or no agent connected to type them. (The agent's `input_automation` switch is not
/// asked per press: at start it decides whether the key is registered at all, `typing_refusal`.)
pub fn cannot_record(console_chosen: bool, console_url: &str, agent_connected: bool) -> Option<String> {
    if !console_chosen {
        return Some("no console to record with yet".into());
    }
    if let Err(why) = console_allows(console_url) {
        return Some(why);
    }
    (!agent_connected).then(|| "the agent is not connected, so nothing could be typed".into())
}

/// What a key going down came to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Down {
    /// An auto-repeat, or a press the machine will not take: nothing to do and nothing to say.
    Ignore,
    /// The press could not start: said once; its auto-repeat stays quiet until the key is up.
    Refused(String),
    /// Armed: the page is to record under this id.
    Armed(String),
}

#[derive(Debug, Default)]
pub struct Machine {
    state: State,
    /// The key is still down from a press that was ended for it (the hold cap, a refusal) and not
    /// let go yet. The OS repeats a held key's down events, and each would arm a new press.
    held_over: bool,
    /// The id of the last answer `accept` took, for the page to be told it has been received.
    answered: Option<String>,
}

/// 128 bits from the OS, as 32 hex digits: the page cannot guess the id of the press it is
/// answering, and the digits sit in a script and a title without a quote. `None` if the OS has no
/// randomness to give, and then no press is armed.
fn random_id() -> Option<String> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).ok()?;
    Some(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

impl Machine {
    /// The key went down, with the reason (if any) it cannot record.
    pub fn down(&mut self, refusal: Option<String>) -> Down {
        if self.held_over {
            return Down::Ignore;
        }
        match refusal {
            Some(why) if self.state == State::Idle => {
                self.held_over = true;
                Down::Refused(why)
            }
            _ => self.press().map_or(Down::Ignore, Down::Armed),
        }
    }

    /// The key went down. `Some(id)` when a press is armed and the page is to record; `None` for
    /// an auto-repeat of the same hold, a hold the cap already ended, or a press while the last
    /// one's words are still awaited.
    pub fn press(&mut self) -> Option<String> {
        if self.held_over || self.state != State::Idle {
            return None;
        }
        let id = random_id()?;
        self.state = State::Held(id.clone());
        Some(id)
    }

    /// The key came up: the id to tell the page, once, if a press was held. Whatever the cap did,
    /// this is the real key-up, and the next down is a new press.
    pub fn release(&mut self) -> Option<String> {
        self.held_over = false;
        match std::mem::take(&mut self.state) {
            State::Held(id) => {
                self.state = State::Waiting(id.clone());
                Some(id)
            }
            other => {
                self.state = other;
                None
            }
        }
    }

    /// The hold cap or the result window ran out for `id`.
    pub fn expire(&mut self, id: &str) -> Expired {
        match &self.state {
            State::Held(held) if held == id => {
                // Let go by the cap, not by the hand: the key is still down.
                self.held_over = true;
                self.state = State::Waiting(id.to_string());
                Expired::Released
            }
            State::Waiting(waiting) if waiting == id => {
                self.state = State::Idle;
                Expired::Abandoned
            }
            _ => Expired::Stale,
        }
    }

    /// The press the last `Text` or `Nothing` answered.
    pub fn answered(&self) -> Option<&str> {
        self.answered.as_deref()
    }

    /// A document title the popup's page set. Takes the one answer, once, for the press awaited.
    pub fn accept(&mut self, title: &str) -> Heard {
        let State::Waiting(id) = &self.state else { return Heard::Ignore };
        let Some(said) = title.split(" · ").skip(1).find_map(|segment| segment.strip_prefix(RESULT_PREFIX)) else {
            return Heard::Ignore;
        };
        let Some((answered, encoded)) = said.split_once(':') else { return Heard::Ignore };
        if answered != id {
            return Heard::Ignore;
        }
        self.answered = Some(answered.to_string());
        self.state = State::Idle;
        match decode(encoded) {
            Some(text) => Heard::Text(text),
            None => Heard::Nothing,
        }
    }
}

/// `encodeURIComponent`'s output back to text; `None` for nothing worth typing. Only the first
/// `key=value` of what is parsed is read, so a raw `&` in a forged segment adds nothing.
fn decode(encoded: &str) -> Option<String> {
    let query = format!("t={encoded}");
    let (_, value) = url::form_urlencoded::parse(query.as_bytes()).next()?;
    let text = value.trim();
    (!text.is_empty() && text.chars().count() <= MAX_TEXT_CHARACTERS).then(|| text.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn contract() -> Value {
        serde_json::from_str(include_str!("../../../../tests/contracts/dictation.json")).unwrap()
    }

    fn title(segment: &str) -> String {
        format!("Jarvis · {segment} · pill")
    }

    fn waiting() -> (Machine, String) {
        let mut machine = Machine::default();
        let id = machine.press().unwrap();
        assert_eq!(machine.release().as_deref(), Some(id.as_str()));
        (machine, id)
    }

    #[test]
    fn a_press_arms_one_fresh_id_and_a_repeat_of_the_hold_arms_nothing() {
        let mut machine = Machine::default();
        let first = machine.press().unwrap();
        assert_eq!(machine.press(), None, "auto-repeat while held");
        assert_eq!(machine.release().as_deref(), Some(first.as_str()));
        assert_eq!(machine.release(), None, "a second release says nothing");
        assert_eq!(machine.press(), None, "the last words are still awaited");
        machine.expire(&first);
        let second = machine.press().unwrap();
        assert_ne!(first, second);
    }

    #[test]
    fn ids_are_the_contract_s_and_safe_in_a_script() {
        let pattern = contract()["id_pattern"].as_str().unwrap().to_string();
        assert_eq!(pattern, "[a-z0-9]{1,32}");
        let mut machine = Machine::default();
        for _ in 0..30 {
            let id = machine.press().unwrap();
            assert!((1..=32).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()));
            machine.release();
            machine.expire(&id);
        }
        assert!(arm_script(true, "d7").contains("down: true, id: 'd7'"));
        assert!(arm_script(false, "d7").contains("'jarvis:dictate'") && arm_script(false, "d7").contains("down: false"));
        assert!(ack_script("d7").contains("'jarvis:dictated'") && ack_script("d7").contains("id: 'd7'"));
    }

    #[test]
    fn an_id_is_random_128_bits_and_never_the_counter_a_page_could_guess() {
        let mut machine = Machine::default();
        let mut seen = std::collections::HashSet::new();
        for _ in 0..200 {
            let id = machine.press().unwrap();
            assert_eq!(id.len(), 32, "{id}: 128 bits as hex");
            assert!(id.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()));
            assert!(seen.insert(id.clone()), "{id} came up twice");
            machine.release();
            machine.expire(&id);
        }
    }

    #[test]
    fn a_guessed_counter_style_id_is_refused() {
        let (mut machine, id) = waiting();
        for n in 0..50 {
            for guess in [format!("d{n}"), n.to_string(), format!("{n:032x}")] {
                assert_eq!(machine.accept(&title(&format!("typed:{guess}:rm%20-rf"))), Heard::Ignore, "{guess}");
            }
        }
        // And the press is still awaiting its own answer: guesses do not spend it.
        assert_eq!(machine.accept(&title(&format!("typed:{id}:the%20real%20one"))), Heard::Text("the real one".into()));
    }

    #[test]
    fn a_key_held_past_the_cap_does_not_re_arm_on_auto_repeat_until_a_real_key_up() {
        let mut machine = Machine::default();
        let id = machine.press().unwrap();
        assert_eq!(machine.expire(&id), Expired::Released, "the cap fires");
        assert_eq!(machine.accept(&title(&format!("typed:{id}:words"))), Heard::Text("words".into()), "the answer arrives");
        assert_eq!(machine.press(), None, "auto-repeat down after the cap: no new press");
        assert_eq!(machine.press(), None);
        assert_eq!(machine.release(), None, "the real key-up says nothing to the page, the cap already did");
        assert!(machine.press().is_some(), "and only then is the next press armed");
    }

    #[test]
    fn a_held_key_after_the_window_forgot_the_press_does_not_re_arm_either() {
        let mut machine = Machine::default();
        let id = machine.press().unwrap();
        machine.expire(&id);
        assert_eq!(machine.expire(&id), Expired::Abandoned);
        assert_eq!(machine.press(), None);
        machine.release();
        assert!(machine.press().is_some());
    }

    #[test]
    fn a_refused_press_is_said_once_and_its_auto_repeat_is_silent() {
        let mut machine = Machine::default();
        assert_eq!(machine.down(Some("no agent".into())), Down::Refused("no agent".into()));
        assert_eq!(machine.down(Some("no agent".into())), Down::Ignore, "the repeat is not said again");
        assert_eq!(machine.down(None), Down::Ignore, "nor armed, while the key is still down");
        machine.release();
        assert!(matches!(machine.down(None), Down::Armed(_)));
        assert_eq!(machine.down(None), Down::Ignore, "a repeat of the armed hold");
    }

    #[test]
    fn the_console_must_be_https_or_loopback_for_the_words_to_cross_to_the_adapter() {
        for ok in ["https://jarvis.example.net", "https://192.168.1.20:8199", "http://127.0.0.1:8199", "http://localhost:8199", "http://[::1]:8199", "http://127.1.2.3"] {
            assert_eq!(console_allows(ok), Ok(()), "{ok}");
        }
        for bad in ["http://192.168.1.20:8199", "http://jarvis.example.net", "http://100.64.0.1:8199", "ftp://localhost", "not a url", ""] {
            let why = console_allows(bad).unwrap_err();
            assert!(why.contains("https") && why.contains("loopback"), "{bad}: {why}");
        }
    }

    #[test]
    fn the_press_is_refused_before_recording_when_there_is_nothing_to_type_with() {
        let lan = "https://jarvis.example.net";
        assert_eq!(cannot_record(true, lan, true), None);
        assert!(cannot_record(false, lan, true).unwrap().contains("console"));
        assert!(cannot_record(true, "http://192.168.1.20", true).unwrap().contains("https"));
        assert!(cannot_record(true, lan, false).unwrap().contains("agent"));
    }

    #[test]
    fn the_contract_s_segments_decode_to_their_words_and_a_null_to_nothing() {
        for case in contract()["cases"].as_array().unwrap() {
            let (mut machine, _) = waiting();
            machine.state = State::Waiting(case["id"].as_str().unwrap().to_string());
            let heard = machine.accept(&title(case["segment"].as_str().unwrap()));
            match case["text"].as_str() {
                Some(text) => assert_eq!(heard, Heard::Text(text.to_string()), "{case}"),
                None => assert_eq!(heard, Heard::Nothing, "{case}"),
            }
        }
    }

    #[test]
    fn an_answer_before_the_key_is_let_go_is_ignored() {
        let mut machine = Machine::default();
        let id = machine.press().unwrap();
        assert_eq!(machine.accept(&title(&format!("typed:{id}:early"))), Heard::Ignore);
        machine.release();
        assert_eq!(machine.accept(&title(&format!("typed:{id}:late%20enough"))), Heard::Text("late enough".into()));
    }

    #[test]
    fn only_the_armed_id_is_answered_and_only_once() {
        let (mut machine, id) = waiting();
        assert_eq!(machine.accept(&title("typed:d999:someone%20else")), Heard::Ignore);
        assert_eq!(machine.accept("Jarvis · working"), Heard::Ignore);
        assert_eq!(machine.accept(&format!("typed:{id}:no%20name%20first")), Heard::Ignore, "the name leads the title");
        assert_eq!(machine.answered(), None, "nothing acknowledged yet");
        assert_eq!(machine.accept(&title(&format!("typed:{id}:once"))), Heard::Text("once".into()));
        assert_eq!(machine.answered(), Some(id.as_str()), "the page is told this press was received");
        assert_eq!(machine.accept(&title(&format!("typed:{id}:twice"))), Heard::Ignore);
    }

    #[test]
    fn a_tap_blank_or_over_long_answer_types_nothing() {
        for answer in ["", "%20%20", "%0A%09"] {
            let (mut machine, id) = waiting();
            assert_eq!(machine.accept(&title(&format!("typed:{id}:{answer}"))), Heard::Nothing, "{answer:?}");
        }
        let (mut machine, id) = waiting();
        let long = "a".repeat(MAX_TEXT_CHARACTERS + 1);
        assert_eq!(machine.accept(&title(&format!("typed:{id}:{long}"))), Heard::Nothing);
        let (mut machine, id) = waiting();
        let fits = "a".repeat(MAX_TEXT_CHARACTERS);
        assert_eq!(machine.accept(&title(&format!("typed:{id}:{fits}"))), Heard::Text(fits));
    }

    #[test]
    fn a_forged_segment_with_a_second_pair_adds_nothing() {
        let (mut machine, id) = waiting();
        assert_eq!(machine.accept(&title(&format!("typed:{id}:hi&t=rm"))), Heard::Text("hi".into()));
    }

    #[test]
    fn the_hold_cap_lets_go_a_stuck_key_and_the_window_forgets_a_silent_page() {
        let mut machine = Machine::default();
        let id = machine.press().unwrap();
        assert_eq!(machine.expire("d999"), Expired::Stale);
        assert_eq!(machine.expire(&id), Expired::Released);
        assert_eq!(machine.release(), None, "the cap already released it");
        assert_eq!(machine.expire(&id), Expired::Abandoned);
        assert_eq!(machine.expire(&id), Expired::Stale);
        assert_eq!(machine.accept(&title(&format!("typed:{id}:late"))), Heard::Ignore);
    }

    #[test]
    fn a_chord_needs_a_modifier_before_it_is_a_dictation_key() {
        assert!(needs_modifier("Super+Space") && needs_modifier("Ctrl+Alt+D") && needs_modifier(" Alt + F9 "));
        assert!(!needs_modifier("F9") && !needs_modifier("Space") && !needs_modifier("") && !needs_modifier("Ctrl+"));
    }

    fn switch_contract() -> Value {
        serde_json::from_str(include_str!("../../../../tests/contracts/input-automation-switch.json")).unwrap()
    }

    #[test]
    fn the_agents_typing_switch_is_read_as_the_agent_reads_it() {
        for case in switch_contract()["switch"].as_array().unwrap() {
            let (env, config) = (case["env"].as_str(), case["config"].as_str());
            assert_eq!(input_automation_on(env, config), case["enabled"].as_bool().unwrap(), "{}", case["name"]);
        }
    }

    #[test]
    fn the_agents_config_file_is_found_where_the_agent_looks() {
        for case in switch_contract()["path"].as_array().unwrap() {
            let path = agent_config_path(
                case["os"].as_str().unwrap(),
                None,
                case["xdg_config_home"].as_str(),
                case["home"].as_str(),
            );
            assert_eq!(path, Some(std::path::PathBuf::from(case["path"].as_str().unwrap())), "{case}");
        }
        let windows = agent_config_path("windows", Some("C:/Roaming"), None, Some("C:/Users/u"));
        assert_eq!(windows, Some(std::path::Path::new("C:/Roaming").join("jarvis-desktop").join("config.json")));
        let no_appdata = agent_config_path("windows", None, None, Some("C:/Users/u"));
        assert_eq!(no_appdata, Some(std::path::Path::new("C:/Users/u/AppData/Roaming/jarvis-desktop/config.json").to_path_buf()));
        assert_eq!(agent_config_path("linux", None, None, None), None, "no home and no XDG: nowhere to look");
        assert_eq!(agent_config_path("linux", None, Some(""), Some("")), None, "an empty home is no home");
    }

    #[test]
    fn with_typing_off_or_unknown_the_key_is_refused_in_plain_words_and_with_it_on_it_is_not() {
        let on = r#"{"input_automation": {"enabled": true}}"#;
        assert_eq!(typing_refusal(None, Some(on)), None);
        assert_eq!(typing_refusal(Some("1"), None), None, "the environment's switch counts");
        for (env, config) in [(None, Some(r#"{"input_automation": {"enabled": false}}"#)), (None, None), (None, Some("not json")), (Some("0"), Some(on))] {
            let why = typing_refusal(env, config).expect("off or unknown is refused");
            assert!(why.contains("typing is switched off") && why.contains("input_automation"), "{why}");
            let line = dictation_tray_label("Jarvis", "Ctrl+Alt+D", Some(&why));
            assert!(line.contains("not available") && line.contains("switched off"), "{line}");
        }
    }

    #[test]
    fn the_tray_line_names_the_chord_or_says_why_it_is_not_ours() {
        assert!(dictation_tray_label("Jarvis", "Ctrl+Alt+D", None).contains("Ctrl+Alt+D"));
        let refused = dictation_tray_label("Jarvis", "F9", Some("it needs a modifier"));
        assert!(refused.contains("not available") && refused.contains("needs a modifier"));
        assert!(dictation_clash("Ctrl+Alt+D", "typing").contains("typing"));
    }
}
