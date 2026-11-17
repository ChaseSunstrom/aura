//! The desktop app's decisions, apart from its windows (ADR-0051). Everything
//! here is pure and tested without a display, a webview or the agent; the app
//! crate (`src-tauri/src`) is what draws, spawns and listens.

pub mod console;
pub mod dictation;
pub mod labels;
pub mod palette;
pub mod protocol;
#[rustfmt::skip]
pub mod states;
pub mod update;
