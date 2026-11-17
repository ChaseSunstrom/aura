//! When the app updates itself (ADR-0050, carried over by ADR-0051).
//!
//! The updater checks hourly and downloads in the background; a downloaded
//! build applies itself only when nothing would notice -- the agent idle or not
//! running (never mid-turn, mid-action or mid-consent), the console not the
//! window being used, and it has sat that way for the settle time. A tray app
//! that runs all day may never quit, so "install when you quit" alone left a
//! build waiting for ever. The agent is inside the app, so it updates with it.

use std::time::Duration;

pub const CHECK_INTERVAL: Duration = Duration::from_secs(60 * 60);
pub const APPLY_SETTLE: Duration = Duration::from_secs(2 * 60);

/// Whether a downloaded update may restart the app now. `downloaded_ago` is
/// `None` while nothing is waiting.
pub fn ready_to_apply(state: &str, window_focused: bool, downloaded_ago: Option<Duration>) -> bool {
    let Some(ago) = downloaded_ago else { return false };
    if state != "idle" && state != "offline" {
        return false;
    }
    !window_focused && ago >= APPLY_SETTLE
}

#[cfg(test)]
mod tests {
    use super::*;

    const LATER: Option<Duration> = Some(Duration::from_secs(3 * 60));

    #[test]
    fn applies_once_idle_unwatched_and_settled() {
        assert!(ready_to_apply("idle", false, LATER));
        assert!(ready_to_apply("offline", false, LATER));
    }

    #[test]
    fn never_mid_turn_mid_action_or_mid_consent() {
        for state in ["thinking", "speaking", "tool", "attention", "listening"] {
            assert!(!ready_to_apply(state, false, LATER), "{state}");
        }
    }

    #[test]
    fn not_while_watched_not_before_the_settle_not_with_nothing_downloaded() {
        assert!(!ready_to_apply("idle", true, LATER));
        assert!(!ready_to_apply("idle", false, Some(Duration::from_secs(30))));
        assert!(!ready_to_apply("idle", false, None));
    }

    #[test]
    fn checks_hourly() {
        assert_eq!(CHECK_INTERVAL, Duration::from_secs(3600));
    }
}
