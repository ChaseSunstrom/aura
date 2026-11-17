//! What the app's own pages may ask the app to do. The console's window is not
//! granted any of these (`capabilities/local.json` names only the local windows,
//! and a remote page gets no IPC at all): the console is a web page from a
//! server, treated exactly as a browser would treat one.

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;

use jarvis_desktop_logic::{labels, protocol};
use crate::shell::{self, CliOutput, PairResult, UpdateInfo};

#[derive(Serialize)]
pub struct ShellState {
    state: String,
    detail: String,
    label: String,
    console_url: String,
    console_chosen: bool,
    agent_running: bool,
    connected: bool,
    waiting_for_pairing: bool,
    last_pair: Option<PairResult>,
    version: String,
    update: UpdateInfo,
    autostart: bool,
}

#[tauri::command]
pub fn shell_state(app: AppHandle) -> ShellState {
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    let version = app.package_info().version.to_string();
    let s = app.state::<shell::Shell>();
    let inner = s.inner.lock().unwrap_or_else(|e| e.into_inner());
    ShellState {
        state: inner.state.clone(),
        detail: inner.detail.clone(),
        label: labels::status_label(&inner.state, &inner.detail),
        console_url: inner.console_url.clone(),
        console_chosen: inner.console_chosen,
        agent_running: inner.agent_running,
        connected: inner.connected,
        waiting_for_pairing: inner.waiting_for_pairing,
        last_pair: inner.last_pair.clone(),
        version,
        update: inner.update.clone(),
        autostart,
    }
}

#[tauri::command]
pub async fn pair(app: AppHandle, text: String) -> PairResult {
    shell::pair(&app, &text).await
}

#[tauri::command]
pub fn pending_ask(app: AppHandle) -> Option<Value> {
    shell::pending_ask(&app)
}

#[tauri::command]
pub fn answer(app: AppHandle, id: String, verdict: String) -> bool {
    shell::answer(&app, &id, &verdict)
}

/// The agent's own CLI, for the pages that replace it: `tiers --json`, `policy
/// set …`, `audit --json`, `doctor`, `status --json`. Anything else is refused.
#[tauri::command]
pub async fn agent_cli(app: AppHandle, args: Vec<String>) -> Result<CliOutput, String> {
    if !protocol::cli_allowed(&args) {
        return Err(format!("the app does not run `{}` for a page", args.first().map(String::as_str).unwrap_or("")));
    }
    shell::run_agent(&app, args).await
}

#[tauri::command]
pub fn restart_agent(app: AppHandle) {
    shell::restart_agent(&app);
}

#[tauri::command]
pub fn agent_log(app: AppHandle) -> Vec<String> {
    shell::log_tail(&app)
}

/// Start with the session, in the tray (`--hidden`), or not.
#[tauri::command]
pub fn set_autostart(app: AppHandle, on: bool) -> Result<bool, String> {
    let launcher = app.autolaunch();
    let done = if on { launcher.enable() } else { launcher.disable() };
    done.map_err(|e| e.to_string())?;
    launcher.is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn check_update(app: AppHandle) {
    shell::check_for_update(&app).await;
}

#[tauri::command]
pub fn open_console(app: AppHandle) {
    shell::show_console(&app);
}

#[tauri::command]
pub fn open_desktop(app: AppHandle, section: String) {
    shell::show_desktop(&app, &section);
}
