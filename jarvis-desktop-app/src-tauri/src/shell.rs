//! The app's running parts (ADR-0051): the agent it starts and watches, the
//! socket it answers the agent's questions on, the windows, the tray and the
//! updater.
//!
//! The agent is the Python device agent frozen into a sidecar binary. The app
//! starts `run`, reads the one line the agent announces its socket on, and
//! restarts it when it stops -- except when it stopped because nothing is paired
//! yet (exit 2), which is the pairing page's to fix, not a crash to retry.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Shortcut};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};

/// The worn theme's name as the console last gave it: the popup's title starts with it
/// ("AURA · listening"). AURA until the console has said, as the default theme is.
static WORN: Mutex<String> = Mutex::new(String::new());

/// The name the windows, tray and notifications say (T-0942): the worn theme's, never a fixed one.
pub fn worn_name() -> String {
    let worn = WORN.lock().map(|n| n.clone()).unwrap_or_default();
    if worn.is_empty() { "AURA".to_string() } else { worn }
}

/// The name a popup title leads with, when it has the console's "name · mood" shape.
pub fn name_of(title: &str) -> Option<&str> {
    title.split_once(" · ").map(|(name, _)| name.trim()).filter(|n| !n.is_empty())
}

fn wear(title: &str) {
    if let (Some(name), Ok(mut worn)) = (name_of(title), WORN.lock()) {
        if *worn != name {
            *worn = name.to_string();
        }
    }
}
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::mpsc;

use jarvis_desktop_logic::console::{self, Pasted};
use jarvis_desktop_logic::{dictation, labels, palette, protocol, states, update};

/// The sidecar's name in `bundle.externalBin`.
pub const AGENT: &str = "jarvis-desktop-agent";
/// The key that starts a turn from anywhere. `Super`, not a letter with
/// Ctrl+Shift: those collide with whatever somebody is typing into.
pub const DEFAULT_PUSH_TO_TALK_KEY: &str = "Super+Space";
/// How many of the agent's own log lines the Status page can show.
const LOG_LINES: usize = 300;
/// The agent's exit code for "no token, no server": nothing is paired yet.
const EXIT_UNPAIRED: i32 = 2;

#[derive(Clone, Default, Serialize)]
pub struct PairResult {
    pub ok: bool,
    pub message: String,
    pub console_url: Option<String>,
}

#[derive(Clone, Default, Serialize)]
pub struct UpdateInfo {
    /// `idle`, `checking`, `current`, `downloading`, `ready`, or what went wrong.
    pub status: String,
    pub version: String,
}

#[derive(Default)]
pub struct Inner {
    pub state: String,
    pub detail: String,
    pub console_url: String,
    pub console_chosen: bool,
    child: Option<CommandChild>,
    pub agent_running: bool,
    pub connected: bool,
    pub waiting_for_pairing: bool,
    outgoing: Option<mpsc::UnboundedSender<String>>,
    asks: VecDeque<Value>,
    log: VecDeque<String>,
    pub last_pair: Option<PairResult>,
    generation: u64,
    backoff_s: u64,
    quitting: bool,
    pub update: UpdateInfo,
    downloaded: Option<(tauri_plugin_updater::Update, Vec<u8>, Instant)>,
    /// What the popup's page last said it is doing (its document title, `mood_of`).
    pub popup_mood: Mood,
    /// Bumped on every mood change, so a delayed hide knows it is stale.
    popup_seq: u64,
    /// The hold-to-dictate key's press (T-1258): which one is armed and whether its words are awaited.
    dictation: dictation::Machine,
    /// Bumped on every island resize, so a shrink waiting out the morph knows it is stale.
    island_seq: u64,
    /// Where the overlay was put when it was last shown, in physical pixels: the
    /// top-left of all the screens together. Kept rather than read back, because a
    /// window manager applies a move later and the first step would map wrong.
    overlay_origin: (i32, i32),
}

/// The assist popup's state as its page reports it in its title
/// (`jarvis-web` `hud/popupMood.ts`): the one thing the app needs to know to
/// keep it up while it matters (the owner, 2026-09-25: "if it asks for
/// approval ... I can't click it without closing Jarvis ... could we have it
/// like unfocus so I can move around my screen and do stuff while it works").
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mood {
    #[default]
    Idle,
    /// A turn is running: stays up, unfocused, while the person works elsewhere.
    Working,
    /// An approval or a question waits: pulled up without taking the focus.
    NeedsYou,
    /// A notice just arrived: pulled up for a moment, likewise.
    Notice,
}

/// `Jarvis · working`, `Jarvis · needs you`, `Jarvis · notice`, each maybe followed by
/// ` · pill` ([popup_size]); anything else is idle.
pub fn mood_of(title: &str) -> Mood {
    title
        .split(" · ")
        .skip(1)
        .find_map(|said| match said {
            "working" => Some(Mood::Working),
            "needs you" => Some(Mood::NeedsYou),
            "notice" => Some(Mood::Notice),
            _ => None,
        })
        .unwrap_or_default()
}

/// The page put its island away (Esc, or another window took the focus, with nothing going on):
/// the title says ` · away`, and the window goes, rather than a clear one staying over the screen.
pub fn is_away(title: &str) -> bool {
    title.split(" · ").skip(1).any(|said| said == "away")
}

/// The popup window's size for the island its page shows (T-0695): round the pill when the
/// title ends ` · pill`, so the clear rest of a card-sized window does not take the clicks at
/// the top of the screen while a turn runs out of the way; the card's room otherwise.
pub fn popup_size(title: &str) -> (f64, f64) {
    if title.contains(" · ") && title.ends_with(" · pill") {
        states::ISLAND_PILL_WINDOW
    } else {
        POPUP_SIZE
    }
}

/// How long an idle popup nobody is looking at stays before it goes.
const POPUP_IDLE_HIDE: Duration = Duration::from_secs(6);

#[derive(Default)]
pub struct Shell {
    pub inner: Mutex<Inner>,
    tray_status: Mutex<Option<MenuItem<Wry>>>,
}

fn with<T>(app: &AppHandle, f: impl FnOnce(&mut Inner) -> T) -> T {
    let shell = app.state::<Shell>();
    let mut inner = shell.inner.lock().unwrap_or_else(|e| e.into_inner());
    f(&mut inner)
}

pub fn log(app: &AppHandle, line: impl Into<String>) {
    let line = line.into();
    eprintln!("{line}");
    with(app, |s| {
        s.log.push_back(line);
        while s.log.len() > LOG_LINES {
            s.log.pop_front();
        }
    });
}

pub fn log_tail(app: &AppHandle) -> Vec<String> {
    with(app, |s| s.log.iter().cloned().collect())
}

/// Tell the app's own pages something changed; they re-read `shell_state`.
fn changed(app: &AppHandle) {
    let _ = app.emit("shell-changed", ());
}

pub fn set_state(app: &AppHandle, state: &str, detail: &str) {
    with(app, |s| {
        s.state = state.to_string();
        s.detail = detail.to_string();
    });
    let label = labels::status_label(state, detail);
    if let Some(item) = app.state::<Shell>().tray_status.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        let _ = item.set_text(&label);
    }
    if let Some(tray) = app.tray_by_id("jarvis") {
        let _ = tray.set_tooltip(Some(format!("{} — {label}", worn_name())));
    }
    changed(app);
}

// --- the agent ---------------------------------------------------------------

/// Start the agent's `run`. A restart bumps the generation, so the old
/// process's exit and the old socket's close are recognised as stale.
pub fn start_agent(app: &AppHandle) {
    let generation = with(app, |s| {
        if s.quitting {
            return None;
        }
        s.generation += 1;
        s.waiting_for_pairing = false;
        Some(s.generation)
    });
    let Some(generation) = generation else { return };
    let command = match app.shell().sidecar(AGENT) {
        Ok(command) => command,
        Err(e) => {
            log(app, format!("the agent is missing from this build: {e}"));
            set_state(app, "offline", "the agent is missing from this build");
            return;
        }
    };
    let spawned = command
        .args(["run"])
        .env("AURA_SHELL_ANNOUNCE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .spawn();
    let (mut events, child) = match spawned {
        Ok(pair) => pair,
        Err(e) => {
            log(app, format!("could not start the agent: {e}"));
            set_state(app, "offline", "the agent could not start");
            return;
        }
    };
    with(app, |s| {
        s.child = Some(child);
        s.agent_running = true;
    });
    set_state(app, "starting", "");
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let started = Instant::now();
        let mut code = None;
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim_end().to_string();
                    match protocol::parse_announcement(&line) {
                        Some((port, token_file)) => connect_link(app.clone(), generation, port, token_file),
                        None if !line.is_empty() => log(&app, line),
                        None => {}
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim_end().to_string();
                    if !line.is_empty() {
                        log(&app, line);
                    }
                }
                CommandEvent::Error(e) => log(&app, format!("agent: {e}")),
                CommandEvent::Terminated(payload) => {
                    code = payload.code;
                    break;
                }
                _ => {}
            }
        }
        agent_exited(&app, generation, code, started.elapsed());
    });
}

fn agent_exited(app: &AppHandle, generation: u64, code: Option<i32>, lived: Duration) {
    let next = with(app, |s| {
        if s.generation != generation {
            return None; // replaced by a restart; the new one is already running
        }
        s.child = None;
        s.agent_running = false;
        s.connected = false;
        s.outgoing = None;
        if s.quitting {
            return None;
        }
        if code == Some(EXIT_UNPAIRED) {
            s.waiting_for_pairing = true;
            return Some(None);
        }
        // A run that lasted a minute was a run, not a crash loop: start over.
        s.backoff_s = if lived > Duration::from_secs(60) { 2 } else { (s.backoff_s.max(1) * 2).min(60) };
        Some(Some(s.backoff_s))
    });
    let Some(delay) = next else { return };
    log(app, format!("the agent stopped (exit {})", code.map_or("unknown".into(), |c| c.to_string())));
    match delay {
        None => set_state(app, "offline", "this computer is not paired yet"),
        Some(seconds) => {
            set_state(app, "offline", &format!("restarting the agent in {seconds} s"));
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(seconds)).await;
                if with(&app, |s| s.generation == generation && s.child.is_none()) {
                    start_agent(&app);
                }
            });
        }
    }
}

/// Stop the running agent (if any) and start a fresh one.
pub fn restart_agent(app: &AppHandle) {
    let old = with(app, |s| {
        s.backoff_s = 0;
        s.child.take()
    });
    if let Some(child) = old {
        let _ = child.kill();
    }
    start_agent(app);
}

/// Stop the agent for good: quitting, or an update about to replace its binary
/// (Windows cannot replace a running executable).
pub fn stop_agent(app: &AppHandle) {
    let old = with(app, |s| {
        s.quitting = true;
        s.child.take()
    });
    if let Some(child) = old {
        let _ = child.kill();
    }
}

#[derive(Serialize)]
pub struct CliOutput {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

/// Run the agent once with `args` and collect what it said.
pub async fn run_agent(app: &AppHandle, args: Vec<String>) -> Result<CliOutput, String> {
    let output = app
        .shell()
        .sidecar(AGENT)
        .map_err(|e| format!("the agent is missing from this build: {e}"))?
        .args(args)
        .output()
        .await
        .map_err(|e| format!("could not run the agent: {e}"))?;
    Ok(CliOutput {
        code: output.status.code(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

// --- the socket --------------------------------------------------------------

fn connect_link(app: AppHandle, generation: u64, port: u16, token_file: PathBuf) {
    tauri::async_runtime::spawn(async move {
        let mut delay = Duration::from_millis(500);
        loop {
            if !with(&app, |s| s.generation == generation && s.child.is_some()) {
                return;
            }
            let token = std::fs::read_to_string(&token_file).map(|t| t.trim().to_string()).unwrap_or_default();
            if !token.is_empty() {
                if let Ok(stream) = TcpStream::connect(("127.0.0.1", port)).await {
                    delay = Duration::from_millis(500);
                    run_link(&app, generation, stream, &token).await;
                }
            }
            tokio::time::sleep(delay).await;
            delay = (delay * 2).min(Duration::from_secs(10));
        }
    });
}

async fn run_link(app: &AppHandle, generation: u64, stream: TcpStream, token: &str) {
    let (read, mut write) = stream.into_split();
    if write.write_all(protocol::hello(token).as_bytes()).await.is_err() {
        return;
    }
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();
    with(app, |s| {
        s.outgoing = Some(tx);
        s.connected = true;
    });
    changed(app);
    let writer = tauri::async_runtime::spawn(async move {
        while let Some(line) = rx.recv().await {
            if write.write_all(line.as_bytes()).await.is_err() {
                break;
            }
        }
    });
    let mut lines = BufReader::new(read).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        match protocol::parse_frame(&line) {
            Some(protocol::Frame::Status { state, detail }) => set_state(app, &state, &detail),
            Some(protocol::Frame::Ask(frame)) => ask(app, frame),
            Some(protocol::Frame::Overlay(frame)) => overlay(app, frame),
            Some(protocol::Frame::Handoff { session }) => handoff(app, &session),
            None => {}
        }
    }
    writer.abort();
    let current = with(app, |s| {
        let current = s.generation == generation;
        if current {
            s.connected = false;
            s.outgoing = None;
        }
        current
    });
    if current {
        // An agent that died mid-step never sends its `hide`: an overlay left up
        // would say Jarvis is using a computer nobody is using, and hold Esc.
        hide_overlay(app);
    }
    changed(app);
}

/// A held action: a native notification (the point of a desktop app is that it
/// can reach somebody looking at something else) and the consent window.
fn ask(app: &AppHandle, frame: Value) {
    let description = frame.get("description").and_then(Value::as_str).unwrap_or("an action").to_string();
    let reason = frame.get("reason").and_then(Value::as_str).unwrap_or_default().to_string();
    with(app, |s| s.asks.push_back(frame));
    let _ = app
        .notification()
        .builder()
        .title(format!("{} {} needs your approval", labels::glyph("attention"), worn_name()))
        .body(format!("{description}\n{reason}").trim())
        .show();
    show_consent(app);
    changed(app);
}

pub fn pending_ask(app: &AppHandle) -> Option<Value> {
    with(app, |s| s.asks.front().cloned())
}

/// Answer one held action; single use. The consent window closes with the last.
pub fn answer(app: &AppHandle, id: &str, verdict: &str) -> bool {
    let Some(line) = protocol::answer(id, verdict) else { return false };
    let (sent, remaining) = with(app, |s| {
        let before = s.asks.len();
        s.asks.retain(|a| a.get("id").and_then(Value::as_str) != Some(id));
        let found = s.asks.len() < before;
        let sent = found && s.outgoing.as_ref().is_some_and(|tx| tx.send(line).is_ok());
        (sent, s.asks.len())
    });
    if remaining == 0 {
        if let Some(window) = app.get_webview_window("consent") {
            let _ = window.destroy();
        }
    }
    changed(app);
    sent
}

/// Closing the consent window is a denial, never a default-allow.
fn deny_all(app: &AppHandle) {
    let ids: Vec<String> = with(app, |s| {
        s.asks.iter().filter_map(|a| a.get("id").and_then(Value::as_str).map(str::to_string)).collect()
    });
    for id in ids {
        answer(app, &id, "denied");
    }
}

// --- windows -----------------------------------------------------------------

/// Local pages are served from the app itself: `tauri://localhost` on Linux and
/// macOS, `http://tauri.localhost` on Windows.
fn is_local(url: &url::Url) -> bool {
    url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
}

/// Whether a console answers at `url`: a TCP connect, not a page load, so the
/// app can say "nothing answered" instead of drawing a blank window.
async fn reachable(url: &str) -> bool {
    let Ok(parsed) = url::Url::parse(url) else { return false };
    let Some(host) = parsed.host_str().map(|h| h.trim_start_matches('[').trim_end_matches(']').to_string()) else {
        return false;
    };
    let port = parsed.port_or_known_default().unwrap_or(80);
    matches!(tokio::time::timeout(Duration::from_secs(3), TcpStream::connect((host.as_str(), port))).await, Ok(Ok(_)))
}

fn unreachable_page(url: &str) -> WebviewUrl {
    let query: String = url::form_urlencoded::Serializer::new(String::new()).append_pair("url", url).finish();
    WebviewUrl::App(format!("unreachable.html?{query}").into())
}

/// Let the console's own pages use the microphone. On Linux the window is
/// WebKitGTK, which wry builds with the media stream switched off and whose
/// permission requests it never answers -- WebKit's default for an unanswered
/// request is "no", so every `getUserMedia` failed and voice could not start.
/// Granted only for microphone capture and only to a page on the console's
/// exact origin; a camera or screen request, or any other page, still gets
/// WebKit's refusal.
/// On Windows WebView2 would ask with a prompt of its own -- a separate window,
/// and the popup hides the moment it loses the focus, taking that prompt with
/// it -- so the same grant is made there instead.
///
/// Not covered: the AppImage does not bundle GStreamer (`bundleMediaFramework`),
/// so capture also needs the host's GStreamer audio source plugins.
fn allow_microphone(window: &tauri::WebviewWindow, allowed: String) {
    // The console's own origin only: the window may also show other loopback,
    // tailnet or LAN pages, and none of those is given the microphone.
    let from_console = move |uri: &str| url::Url::parse(uri).is_ok_and(|uri| console::origin_of(&uri) == allowed);
    #[cfg(target_os = "linux")]
    let _ = window.with_webview(move |webview| {
        use webkit2gtk::glib::prelude::{Cast, ObjectExt};
        use webkit2gtk::{PermissionRequestExt, SettingsExt, UserMediaPermissionRequestExt, WebViewExt};
        let view = webview.inner();
        if let Some(settings) = view.settings() {
            settings.set_enable_media_stream(true);
        }
        view.connect_permission_request(move |view, request| {
            let Some(media) = request.downcast_ref::<webkit2gtk::UserMediaPermissionRequest>() else { return false };
            let page = view.uri().map(|uri| uri.to_string()).unwrap_or_default();
            // A screen share asks with is-for-display-device (WebKitGTK 2.38+); older
            // builds have no such property, and their audio request is a microphone.
            let display = media.find_property("is-for-display-device").is_some()
                && media.property::<bool>("is-for-display-device");
            if from_console(&page) && media.is_for_audio_device() && !media.is_for_video_device() && !display {
                request.allow();
                return true;
            }
            false
        });
    });
    #[cfg(windows)]
    let _ = window.with_webview(move |webview| unsafe {
        use webview2_com::Microsoft::Web::WebView2::Win32::*;
        use webview2_com::PermissionRequestedEventHandler;
        let Ok(core) = webview.controller().CoreWebView2() else { return };
        let mut token = Default::default();
        let handler = PermissionRequestedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()) };
            let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
            args.PermissionKind(&mut kind)?;
            let mut uri = windows::core::PWSTR::null();
            args.Uri(&mut uri)?;
            if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE && from_console(&webview2_com::take_pwstr(uri)) {
                args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
            }
            Ok(())
        }));
        let _ = core.add_PermissionRequested(&handler, &mut token);
    });
    #[cfg(not(any(target_os = "linux", windows)))]
    let _ = (window, from_console);
}

/// The console, in the main window. Nothing answering is the first-run case,
/// not a fault to leave as a blank window: the window says which address was
/// tried, and the app keeps trying.
pub fn show_console(app: &AppHandle) {
    show_console_at(app, None);
}

/// The console window, opened at `at` (a handoff's session) or wherever it was.
fn show_console_at(app: &AppHandle, at: Option<url::Url>) {
    if let Some(window) = app.get_webview_window("console") {
        if let Some(url) = at {
            let _ = window.navigate(url);
        }
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let url = with(&app, |s| s.console_url.clone());
        let Ok(parsed) = url::Url::parse(&url) else { return };
        let up = reachable(&url).await;
        let start = if up { WebviewUrl::External(at.unwrap_or(parsed)) } else { unreachable_page(&url) };
        let allowed = console::origin_of(&url::Url::parse(&url).unwrap_or_else(|_| "http://127.0.0.1".parse().unwrap()));
        let mic_origin = allowed.clone();
        let built = WebviewWindowBuilder::new(&app, "console", start)
            .title(worn_name())
            .inner_size(1180.0, 820.0)
            .on_navigation(move |to| {
                // The console and the app's own pages. A link to anywhere else
                // (loopback, the tailnet and the LAN excepted) is refused
                // rather than loaded into the window that holds the console.
                is_local(to) || console::origin_of(to) == allowed || console::is_allowed_console(to.as_str())
            })
            .build();
        match built {
            Ok(window) => {
                allow_microphone(&window, mic_origin);
                let handle = window.clone();
                window.on_window_event(move |event| {
                    // Closing leaves the assistant running, like every tray app:
                    // quitting is a menu item.
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = handle.hide();
                    }
                });
                if !up {
                    retry_console(app.clone());
                }
            }
            Err(e) => log(&app, format!("could not open the console window: {e}")),
        }
    });
}

/// Every five seconds, look again; load the console the moment it answers.
fn retry_console(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(5)).await;
            let Some(window) = app.get_webview_window("console") else { return };
            let url = with(&app, |s| s.console_url.clone());
            if reachable(&url).await {
                if let Ok(parsed) = url::Url::parse(&url) {
                    let _ = window.navigate(parsed);
                }
                return;
            }
        }
    });
}

/// Point the console window at a newly chosen console (and drop a popup still
/// showing the old one, so the next push-to-talk opens the new one).
fn load_console(app: &AppHandle, url: &str) {
    if let Some(popup) = app.get_webview_window("popup") {
        let _ = popup.destroy();
    }
    match app.get_webview_window("console") {
        Some(window) => {
            if let Ok(parsed) = url::Url::parse(url) {
                let _ = window.navigate(parsed);
            }
            let _ = window.show();
        }
        None => show_console(app),
    }
    prepare_popup(app);
}

/// The app's own window: status, pairing, permissions, activity, checks, updates.
pub fn show_desktop(app: &AppHandle, section: &str) {
    if let Some(window) = app.get_webview_window("desktop") {
        let _ = window.eval(format!("location.hash = {}", serde_json::to_string(section).unwrap_or_default()));
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, "desktop", WebviewUrl::App(format!("index.html#{section}").into()))
        .title(format!("{} — this computer", worn_name()))
        .inner_size(960.0, 720.0)
        .build();
    if let Err(e) = built {
        log(app, format!("could not open the settings window: {e}"));
    }
}

fn show_consent(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("consent") {
        let _ = window.eval("window.dispatchEvent(new Event('jarvis-ask'))");
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, "consent", WebviewUrl::App("consent.html".into()))
        .title(format!("{} needs your approval", worn_name()))
        .inner_size(520.0, 440.0)
        .always_on_top(true)
        .focused(true)
        .build();
    match built {
        Ok(window) => {
            let app = app.clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { .. } = event {
                    deny_all(&app);
                }
            });
        }
        Err(e) => log(app, format!("could not open the consent window: {e}")),
    }
}

/// The popup's size while its island is a card (T-0695): the card, `material.floating.card-width`
/// wide and as tall as what it shows, hangs from the top of a clear window with room round it
/// for its veil; [popup_size] shrinks the window round the pill.
const POPUP_SIZE: (f64, f64) = (616.0, 416.0);

/// `<console>/?popup=1`: the console's compact assist view, running the same
/// voice loop as the full console.
fn popup_url(console_url: &str) -> Option<url::Url> {
    let mut url = url::Url::parse(console_url).ok()?;
    url.query_pairs_mut().append_pair("popup", "1");
    Some(url)
}

/// The assist popup, raised for the person: shown and focused.
fn show_popup(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    if let Some(window) = app.get_webview_window("popup") {
        popup_appear(app, &window, true);
        return Some(window);
    }
    build_popup(app, true)
}

/// Bring the popup up with its arrival played (the owner, 2026-10-07: "it just looks
/// like a box ... the animation is rough"). The page is told first (`data-float="in"`
/// on its root, `Hud.svelte`), so the card rises, scales and fades in while the window
/// glides up into its place, rather than a rectangle popping into being. Bumping the
/// sequence cancels a [popup_leave] still waiting to hide it.
fn popup_appear(app: &AppHandle, window: &tauri::WebviewWindow, focus: bool) {
    with(app, |s| s.popup_seq += 1);
    let _ = window.eval("document.documentElement.dataset.float = 'in'");
    if !window.is_visible().unwrap_or(false) {
        // Up as a card (the page is focused or pulled up for something); a pill shrinks it after.
        with(app, |s| s.island_seq += 1);
        let _ = window.set_size(tauri::LogicalSize::new(POPUP_SIZE.0, POPUP_SIZE.1));
        if let Some((x, y)) = popup_home(window) {
            // From a little above its place, so the glide reads as coming down from the top edge.
            let _ = window.set_position(tauri::LogicalPosition::new(x, y - POPUP_SIZE.1 * POPUP_RISE));
            let _ = window.show();
            glide(window.clone(), (x, y));
        } else {
            let _ = window.show();
        }
    }
    if focus {
        let _ = window.set_focus();
    }
}

/// Play the page's exit, then hide -- unless something raised the popup again while
/// it was leaving (a newer sequence number). Hiding at once cut the card off mid-air.
fn popup_leave(app: &AppHandle, window: &tauri::WebviewWindow) {
    if !window.is_visible().unwrap_or(false) {
        return;
    }
    let seq = with(app, |s| {
        s.popup_seq += 1;
        s.popup_seq
    });
    let _ = window.eval("document.documentElement.dataset.float = 'out'");
    let app = app.clone();
    let window = window.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(states::FLOAT_OUT_MS)).await;
        if with(&app, |s| s.popup_seq == seq) {
            let _ = window.hide();
        }
    });
}

/// Where the popup lives: top centre of its screen, under any top bar (the work area), so the
/// island hangs from the top edge the way the phone's does (T-0695).
fn popup_home(window: &tauri::WebviewWindow) -> Option<(f64, f64)> {
    let monitor = window.current_monitor().ok()??;
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let size = area.size.to_logical::<f64>(scale);
    let origin = area.position.to_logical::<f64>(scale);
    Some((origin.x + (size.width - POPUP_SIZE.0) / 2.0, origin.y))
}

/// Fit the window to the island (T-0695), keeping its top centre where it is: at once when it
/// grows, so the morph is never clipped, and once the morph has settled when it shrinks, unless
/// something grew it again meanwhile. Where the platform will not size windows it stays a card's.
fn popup_fit(app: &AppHandle, window: &tauri::WebviewWindow, to: (f64, f64)) {
    let Ok(scale) = window.scale_factor() else { return };
    let (Ok(size), Ok(at)) = (window.inner_size(), window.outer_position()) else { return };
    let (size, at) = (size.to_logical::<f64>(scale), at.to_logical::<f64>(scale));
    // Bumped before the size check: a pill reopened inside the settle time finds the window
    // still card-sized and returns here, and the shrink it scheduled must not fire after it.
    let seq = with(app, |s| {
        s.island_seq += 1;
        s.island_seq
    });
    if (size.width, size.height) == to {
        return;
    }
    let centre = at.x + size.width / 2.0;
    let fit = move |window: &tauri::WebviewWindow| {
        let _ = window.set_size(tauri::LogicalSize::new(to.0, to.1));
        let _ = window.set_position(tauri::LogicalPosition::new(centre - to.0 / 2.0, at.y));
    };
    if to.0 >= size.width || to.1 >= size.height {
        fit(window);
        return;
    }
    let app = app.clone();
    let window = window.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(states::ISLAND_SETTLE_MS)).await;
        if with(&app, |s| s.island_seq == seq) {
            fit(&window);
        }
    });
}

/// Move a floating window to `to` over `motion.dur.glide`, eased out (fast, then
/// settling), instead of jumping there. Where the platform will not place windows
/// (Wayland) each step is a no-op and the window simply stays where it is.
fn glide(window: tauri::WebviewWindow, to: (f64, f64)) {
    tauri::async_runtime::spawn(async move {
        let Ok(scale) = window.scale_factor() else { return };
        let Ok(from) = window.outer_position() else { return };
        let from = from.to_logical::<f64>(scale);
        let steps = (states::GLIDE_MS / GLIDE_FRAME_MS).max(1);
        for i in 1..=steps {
            tokio::time::sleep(Duration::from_millis(GLIDE_FRAME_MS)).await;
            let t = i as f64 / steps as f64;
            let eased = 1.0 - (1.0 - t).powi(3);
            let _ = window.set_position(tauri::LogicalPosition::new(
                from.x + (to.0 - from.x) * eased,
                from.y + (to.1 - from.y) * eased,
            ));
        }
    });
}

/// One frame of a [glide] at 60 Hz.
const GLIDE_FRAME_MS: u64 = 16;
/// How far above its place the popup starts its glide, as a share of its height.
const POPUP_RISE: f64 = 0.12;

/// Make the popup, hidden, as soon as there is a console: its page is what
/// hears an approval or a notice arrive, so it has to be running before
/// anyone presses the key for a notice to pull it up.
pub fn prepare_popup(app: &AppHandle) {
    if app.get_webview_window("popup").is_none() && with(app, |s| s.console_chosen) {
        build_popup(app, false);
    }
}

/// The page said what it is doing. Waiting on the person, or a notice: up,
/// without the focus, so whatever they are typing into keeps it. Idle and not
/// looked at: gone after a moment. Working: left as it is.
fn popup_mood_changed(app: &AppHandle, window: &tauri::WebviewWindow, mood: Mood) {
    let seq = with(app, |s| {
        s.popup_mood = mood;
        s.popup_seq += 1;
        s.popup_seq
    });
    match mood {
        Mood::NeedsYou | Mood::Notice => {
            if !window.is_visible().unwrap_or(false) {
                popup_appear(app, window, false);
            }
        }
        Mood::Idle => {
            let app = app.clone();
            let window = window.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(POPUP_IDLE_HIDE).await;
                let still = with(&app, |s| s.popup_seq == seq && s.popup_mood == Mood::Idle);
                if still && !window.is_focused().unwrap_or(false) {
                    popup_leave(&app, &window);
                }
            });
        }
        Mood::Working => {}
    }
}

/// The assist popup: frameless, above everything, at the top of the screen. It hides
/// when focus goes elsewhere only while it is idle: a turn running or an
/// approval waiting stays on screen while the person works in other windows.
fn build_popup(app: &AppHandle, visible: bool) -> Option<tauri::WebviewWindow> {
    let url = with(app, |s| s.console_url.clone());
    let target = popup_url(&url)?;
    let allowed = console::origin_of(&target);
    let mic_origin = allowed.clone();
    let builder = WebviewWindowBuilder::new(app, "popup", WebviewUrl::External(target))
        .title(worn_name())
        .inner_size(POPUP_SIZE.0, POPUP_SIZE.1)
        .decorations(false)
        .shadow(false);
    // The page draws the card -- rounded, translucent, lit by the reactor -- so the
    // window itself is clear and casts no rectangle of its own around it. macOS gates
    // a transparent window behind Tauri's private-API feature, which this app does not
    // take; there the popup keeps its dark ground, rounded by the page.
    #[cfg(not(target_os = "macos"))]
    let builder = builder.transparent(true);
    let built = builder
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible(visible)
        .focused(visible)
        .on_navigation(move |to| console::origin_of(to) == allowed)
        .on_document_title_changed({
            let app = app.clone();
            move |window, title| {
                popup_fit(&app, &window, popup_size(&title));
                wear(&title);
                dictation_heard(&app, &title);
                popup_mood_changed(&app, &window, mood_of(&title));
                if is_away(&title) {
                    popup_leave(&app, &window);
                }
            }
        })
        .build();
    let window = match built {
        Ok(window) => window,
        Err(e) => {
            log(app, format!("could not open the popup: {e}"));
            return None;
        }
    };
    // Push-to-talk records here, not in the console window.
    allow_microphone(&window, mic_origin);
    if let Some((x, y)) = popup_home(&window) {
        let _ = window.set_position(tauri::LogicalPosition::new(x, y));
    }
    let handle = window.clone();
    let events = app.clone();
    window.on_window_event(move |event| match event {
        WindowEvent::Focused(false) => {
            if with(&events, |s| s.popup_mood == Mood::Idle) {
                popup_leave(&events, &handle);
            }
        }
        WindowEvent::CloseRequested { api, .. } => {
            api.prevent_close();
            popup_leave(&events, &handle);
        }
        _ => {}
    });
    Some(window)
}

/// "Show it on my desktop" (T-0617): the console, here, on the session the owner
/// was looking at on another device (`jarvis-web` opens `?session=`). The id was
/// held to `tests/contracts/handoff-session.json` on the way in
/// (`protocol::parse_frame`) and goes in as a query value, never as a path or an
/// address: a handoff can only open this app's own console, never another site.
fn handoff(app: &AppHandle, session: &str) {
    let console = with(app, |s| s.console_chosen.then(|| s.console_url.clone()));
    let Some(mut url) = console.and_then(|u| url::Url::parse(&u).ok()) else { return };
    url.query_pairs_mut().append_pair("session", session);
    log(app, format!("opening session {session}, handed off from another device"));
    show_console_at(app, Some(url));
}

// --- the visible path (T-0617) -------------------------------------------------

/// The overlay: a clear window over every screen that draws what the agent is
/// about to do (`ui/overlay.ts`). Made once, hidden, at start: a window made for
/// each step would take the focus from what the agent is typing into.
pub fn prepare_overlay(app: &AppHandle) {
    if app.get_webview_window("overlay").is_none() {
        build_overlay(app);
    }
}

#[cfg(not(target_os = "macos"))]
fn build_overlay(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    let built = WebviewWindowBuilder::new(app, "overlay", WebviewUrl::App("overlay.html".into()))
        .title(format!("{} is using this computer", worn_name()))
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .focusable(false)
        .visible(false)
        .build();
    let window = match built {
        Ok(window) => window,
        Err(e) => {
            log(app, format!("could not make the overlay: {e}"));
            return None;
        }
    };
    // A clear window over every screen that took the clicks would lock the owner
    // out of their own computer, take-over included. No click-through, no overlay:
    // the agent still works, and the tray still says what it is doing.
    if let Err(e) = window.set_ignore_cursor_events(true) {
        log(app, format!("the overlay could not let clicks through ({e}); working without it"));
        let _ = window.destroy();
        return None;
    }
    Some(window)
}

/// macOS keeps a clear window behind Tauri's private API, which this app does not
/// take (`build_popup`): an opaque one over every screen would hide them all.
#[cfg(target_os = "macos")]
fn build_overlay(_app: &AppHandle) -> Option<tauri::WebviewWindow> {
    None
}

/// Lay the overlay over all the screens together, in physical pixels -- the
/// space the agent's input backend moves in -- and say where that starts.
/// Does not cover screens with different scales: the page maps with one.
fn cover_screens(window: &tauri::WebviewWindow) -> (i32, i32) {
    let monitors = window.available_monitors().unwrap_or_default();
    let Some(first) = monitors.first() else { return (0, 0) };
    let (mut x0, mut y0) = (first.position().x, first.position().y);
    let (mut x1, mut y1) = (x0, y0);
    for m in &monitors {
        let (p, s) = (m.position(), m.size());
        x0 = x0.min(p.x);
        y0 = y0.min(p.y);
        x1 = x1.max(p.x.saturating_add(s.width as i32));
        y1 = y1.max(p.y.saturating_add(s.height as i32));
    }
    let _ = window.set_position(tauri::PhysicalPosition::new(x0, y0));
    let _ = window.set_size(tauri::PhysicalSize::new(x1.abs_diff(x0), y1.abs_diff(y0)));
    (x0, y0)
}

/// One step of the visible path: the overlay comes up for the first, goes on
/// `hide`, and the page draws the step (or refuses one it cannot: `overlayFrame`).
fn overlay(app: &AppHandle, frame: Value) {
    let Some(window) = app.get_webview_window("overlay") else { return };
    let op = frame.get("op").and_then(Value::as_str).unwrap_or_default();
    if op == "hide" {
        hide_overlay(app);
        return;
    }
    // A step the page would not draw must not put up an empty window that holds Esc.
    if !["path", "highlight", "ripple", "caret", "scroll", "key"].contains(&op) {
        return;
    }
    if !window.is_visible().unwrap_or(false) {
        let origin = cover_screens(&window);
        with(app, |s| s.overlay_origin = origin);
        // Again before every show: a platform that dropped it while the window was
        // hidden must not put a click-eating pane over the screens.
        if window.set_ignore_cursor_events(true).is_err() || window.show().is_err() {
            let _ = window.hide();
            return;
        }
    }
    // The agent's own Esc has to reach the window it means it for: the guard lets
    // go for that step and takes Esc again on the next.
    let own_escape = op == "key"
        && frame.get("keys").and_then(Value::as_str).is_some_and(|k| k.to_ascii_lowercase().contains("esc"));
    guard_escape(app, !own_escape);
    let scale = window.scale_factor().unwrap_or(1.0);
    let (x, y) = with(app, |s| s.overlay_origin);
    let screen = serde_json::json!({ "left": f64::from(x) / scale, "top": f64::from(y) / scale, "scale": scale });
    let _ = app.emit_to("overlay", "overlay", serde_json::json!({ "frame": frame, "screen": screen }));
}

/// Hide the overlay and give Esc back to whatever has the focus: on the agent's
/// `hide`, on the owner's take-over, and when the agent's link drops.
fn hide_overlay(app: &AppHandle) {
    guard_escape(app, false);
    let _ = app.emit_to("overlay", "overlay", serde_json::json!({ "frame": { "op": "hide" } }));
    if let Some(window) = app.get_webview_window("overlay") {
        let _ = window.hide();
    }
}

/// Esc with no modifier: the owner's "I have it" while the agent works.
pub fn escape_key() -> Shortcut {
    Shortcut::new(None, Code::Escape)
}

/// Hold Esc while the overlay is up, and only then: held all the time it would
/// take the key from every app on the computer.
fn guard_escape(app: &AppHandle, on: bool) {
    let shortcuts = app.global_shortcut();
    if shortcuts.is_registered(escape_key()) == on {
        return;
    }
    let done = if on { shortcuts.register(escape_key()) } else { shortcuts.unregister(escape_key()) };
    if let Err(e) = done {
        log(app, format!("could not {} Esc for take-over: {e}", if on { "hold" } else { "let go of" }));
    }
}

/// The owner pressed Esc while the agent worked: it stops before its next input
/// (`visible.py`, which also knows an Esc it pressed itself) and the tray says
/// "paused, you have it". Called from the shortcut plugin's handler, which holds
/// the plugin's lock while it runs: letting go of Esc in here would wait on that
/// lock for ever, so the hide is a task of its own.
pub fn owner_escape(app: &AppHandle) {
    if let Some(tx) = with(app, |s| s.outgoing.clone()) {
        let _ = tx.send(protocol::takeover());
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move { hide_overlay(&app) });
}

/// The tray's "Resume Jarvis": the owner's word, the only one that ends a take-over.
fn resume_agent(app: &AppHandle) {
    if let Some(tx) = with(app, |s| s.outgoing.clone()) {
        let _ = tx.send(protocol::resume());
    }
}

/// Start (or end) a turn from anywhere, in the popup. `Some(true)` is the key
/// going down, `Some(false)` coming up -- held to talk, like the console's own
/// Space -- and `None` (a tray click, which has no release) toggles. The popup
/// is shown first: the microphone is the console's, so a key that did not raise
/// a window would start a turn nobody can see or stop. The console listens for
/// exactly this event (`jarvis:push-to-talk`, `detail.down`).
pub fn push_to_talk(app: &AppHandle, down: Option<bool>) {
    if down != Some(false) {
        if with(app, |s| !s.console_chosen) {
            show_desktop(app, "pair");
            return;
        }
        show_popup(app);
    }
    let detail = match down {
        Some(d) => format!("{{ down: {d} }}"),
        None => "{}".to_string(),
    };
    if let Some(window) = app.get_webview_window("popup") {
        let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('jarvis:push-to-talk', {{ detail: {detail} }}))"));
    }
}

/// The typing key (T-0696), or the tray's line for it: show the island with its line focused,
/// from any window. The popup's page does the focusing (`Popup.svelte`, on `jarvis:summon`);
/// this raises the window first, as [push_to_talk] does, so a press is never invisible. Before a
/// house is paired there is no console to type to: the pairing page opens instead.
pub fn summon_to_type(app: &AppHandle) {
    if with(app, |s| !s.console_chosen) {
        show_desktop(app, "pair");
        return;
    }
    show_popup(app);
    if let Some(window) = app.get_webview_window("popup") {
        let _ = window.eval(palette::SUMMON_TO_TYPE_SCRIPT);
    }
}

/// Register the typing key and return the tray's line for it: the chord, or why it is not
/// available. A key another app owns, one this app cannot parse and one that is the
/// push-to-talk key are each logged and said in the tray, not left as a key that does nothing.
/// `main.rs` builds the plugin's handler from the same parse, so a refused key is also one the
/// handler never sees.
pub fn register_palette_key(app: &AppHandle, key: &str, push_to_talk: Option<Shortcut>) -> String {
    let name = worn_name();
    let refused = |why: String| {
        log(app, format!("could not register {key} for typing: {why}"));
        palette::palette_tray_label(&name, key, Some(&why))
    };
    let shortcut = match key.parse::<Shortcut>() {
        Ok(shortcut) => shortcut,
        Err(e) => return refused(format!("not a key this app can register: {e}")),
    };
    if push_to_talk == Some(shortcut) {
        return refused(palette::palette_clash(key));
    }
    match app.global_shortcut().register(shortcut) {
        Ok(()) => palette::palette_tray_label(&name, key, None),
        Err(e) => refused(format!("another app may hold it: {e}")),
    }
}

/// The hold-to-dictate key (T-1258), down or up. Down tells the popup's page -- the console's own
/// recorder, hidden, so the focus stays in the window the words are for -- to record; up (or the
/// hold cap, for a stuck key) tells it to stop, transcribe and answer in its title
/// ([dictation_heard]). Called from the shortcut plugin's handler, which holds the plugin's lock:
/// the waits are tasks of their own. With no console chosen or no popup built there is nothing to
/// record with, and the press is logged rather than raised as a window the person did not ask for.
pub fn dictate_key(app: &AppHandle, pressed: bool) {
    if pressed {
        // Refused before the page records a word nobody could type, or that could not be sent
        // safely: no popup, no console, a console the words may not cross to, no agent. The
        // agent's own input switch is not known here, so with it off the words are heard and
        // then dropped by the agent.
        let window = app.get_webview_window("popup");
        let popup_built = window.is_some();
        let down = with(app, |s| {
            let why = if popup_built {
                dictation::cannot_record(s.console_chosen, &s.console_url, s.outgoing.is_some())
            } else {
                Some("no popup to record with yet".to_string())
            };
            s.dictation.down(why)
        });
        let id = match down {
            dictation::Down::Ignore => return,
            dictation::Down::Refused(why) => return log(app, format!("dictation: {why}")),
            dictation::Down::Armed(id) => id,
        };
        if let Some(window) = &window {
            let _ = window.eval(dictation::arm_script(true, &id));
        }
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(dictation::MAX_HOLD).await;
            if with(&app, |s| s.dictation.expire(&id)) == dictation::Expired::Released {
                log(&app, "dictation: the key was held too long; stopped");
                dictation_let_go(&app, id);
            }
        });
    } else if let Some(id) = with(app, |s| s.dictation.release()) {
        dictation_let_go(app, id);
    }
}

/// The page is told the key is up, and the press is forgotten if no answer comes in time.
fn dictation_let_go(app: &AppHandle, id: String) {
    if let Some(window) = app.get_webview_window("popup") {
        let _ = window.eval(dictation::arm_script(false, &id));
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(dictation::RESULT_WINDOW).await;
        if with(&app, |s| s.dictation.expire(&id)) == dictation::Expired::Abandoned {
            log(&app, "dictation: no answer from the page; nothing typed");
        }
    });
}

/// A title the popup's page set, looked at for the answer to a press. The words go to the agent,
/// which types them (`aura_desktop/dictation.py`); anything the page's title said that is not
/// this press's one answer is ignored (`dictation::Machine::accept`). The text itself is never
/// logged: it is what the owner said.
fn dictation_heard(app: &AppHandle, title: &str) {
    let heard = with(app, |s| s.dictation.accept(title));
    if heard != dictation::Heard::Ignore {
        // The words leave the title the moment the app has them, not when the page's timer runs
        // out: a window list or a taskbar would show them for that long.
        if let (Some(window), Some(id)) = (app.get_webview_window("popup"), with(app, |s| s.dictation.answered().map(str::to_string))) {
            let _ = window.eval(dictation::ack_script(&id));
        }
    }
    match heard {
        dictation::Heard::Ignore => {}
        dictation::Heard::Nothing => log(app, "dictation: nothing to type"),
        dictation::Heard::Text(text) => match with(app, |s| s.outgoing.clone()) {
            Some(tx) if tx.send(protocol::dictate(&text)).is_ok() => {
                log(app, format!("dictation: sent {} characters to type", text.chars().count()));
            }
            _ => log(app, "dictation: the agent is not connected; nothing typed"),
        },
    }
}

/// Register the dictation key, returning the tray's line for it. A chord with no modifier, one
/// another key of this app's already is, one this app cannot parse or one another app holds is
/// logged and said in the tray. `main.rs` makes the plugin's handler from [dictation_shortcut].
pub fn register_dictation_key(app: &AppHandle, key: &str, others: &[(Option<Shortcut>, &str)]) -> String {
    let name = worn_name();
    let refused = |why: String| {
        log(app, format!("could not register {key} for dictation: {why}"));
        dictation::dictation_tray_label(&name, key, Some(&why))
    };
    // Before anything is registered: with the agent's typing switched off, or a console the words
    // may not cross to, the key stays unregistered, so it is never taken from the other windows
    // and nothing is recorded for words that would be dropped. Read once, here: a switch turned
    // on later is picked up when the app is restarted.
    if let Some(why) = agent_typing_refusal() {
        return refused(why);
    }
    if let Err(why) = dictation::console_allows(&with(app, |s| s.console_url.clone())) {
        return refused(why);
    }
    match dictation_shortcut(key, others) {
        Err(why) => refused(why),
        Ok(shortcut) => match app.global_shortcut().register(shortcut) {
            Ok(()) => dictation::dictation_tray_label(&name, key, None),
            Err(e) => refused(format!("another app may hold it: {e}")),
        },
    }
}

/// The agent's typing switch, from the config file and variable it reads itself (the app starts it
/// with no `--config`); see `dictation::typing_refusal`. A file that cannot be read is off.
fn agent_typing_refusal() -> Option<String> {
    let var = |name: &str| std::env::var_os(name).map(|v| v.to_string_lossy().into_owned());
    let home = var("HOME").or_else(|| var("USERPROFILE"));
    let path = dictation::agent_config_path(std::env::consts::OS, var("APPDATA").as_deref(), var("XDG_CONFIG_HOME").as_deref(), home.as_deref());
    let text = path.and_then(|p| std::fs::read_to_string(p).ok());
    dictation::typing_refusal(var("AURA_INPUT_ENABLED").as_deref(), text.as_deref())
}

/// The dictation chord as a shortcut, or why it is not one: it needs a modifier (or it would take
/// that letter from every window), it must parse and it must not be one of `others` (labelled).
pub fn dictation_shortcut(key: &str, others: &[(Option<Shortcut>, &str)]) -> Result<Shortcut, String> {
    if !dictation::needs_modifier(key) {
        return Err("it needs a modifier (Ctrl, Alt, Shift or Super) so it is never plain typing".into());
    }
    let shortcut = key.parse::<Shortcut>().map_err(|e| format!("not a key this app can register: {e}"))?;
    match others.iter().find(|(other, _)| *other == Some(shortcut)) {
        Some((_, label)) => Err(dictation::dictation_clash(key, label)),
        None => Ok(shortcut),
    }
}

// --- pairing -----------------------------------------------------------------

fn device_name() -> String {
    std::env::var("COMPUTERNAME")
        .ok()
        .or_else(|| std::fs::read_to_string("/etc/hostname").ok())
        .map(|name| name.trim().to_string())
        .unwrap_or_default()
}

pub fn config_dir(app: &AppHandle) -> PathBuf {
    app.path().app_config_dir().unwrap_or_else(|_| std::env::temp_dir().join("jarvis-desktop-app"))
}

/// What the pairing page, a pasted link and a clicked `jarvis://` link all do:
/// read it, pair the agent for a link, keep the console, load it.
pub async fn pair(app: &AppHandle, text: &str) -> PairResult {
    let read = console::read_pasted(text);
    let result = match &read {
        Pasted::Refused(why) => PairResult { ok: false, message: why.clone(), console_url: None },
        Pasted::Console { console_url } | Pasted::Link { console_url, .. } => {
            let mut note = String::new();
            let mut paired = false;
            if let Some(args) = console::agent_pair_args(&read, &device_name()) {
                match run_agent(app, args).await {
                    Ok(out) if out.code == Some(0) => {
                        paired = true;
                        note = " This computer is paired too.".into();
                    }
                    Ok(out) => {
                        let said = if out.stderr.trim().is_empty() { out.stdout } else { out.stderr };
                        let first = said.trim().lines().next().unwrap_or("no reason given").to_string();
                        note = format!(" This computer could not pair: {first}");
                    }
                    Err(e) => note = format!(" This computer could not pair: {e}"),
                }
            }
            match console::save_console(&config_dir(app), console_url) {
                Err(e) => PairResult { ok: false, message: format!("could not keep the console: {e}"), console_url: None },
                Ok(()) => {
                    with(app, |s| {
                        s.console_url = console_url.clone();
                        s.console_chosen = true;
                    });
                    load_console(app, console_url);
                    if paired {
                        restart_agent(app);
                    }
                    PairResult { ok: true, message: format!("Connecting to {console_url}.{note}"), console_url: Some(console_url.clone()) }
                }
            }
        }
    };
    with(app, |s| s.last_pair = Some(result.clone()));
    changed(app);
    result
}

// --- the tray ----------------------------------------------------------------

pub fn build_tray(
    app: &AppHandle,
    push_to_talk_key: &str,
    palette_label: &str,
    dictation_label: Option<&str>,
) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", labels::status_label("offline", ""), false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", &format!("Open {}", worn_name()), true, None::<&str>)?;
    let talk = MenuItem::with_id(app, "talk", format!("Push to talk ({push_to_talk_key})"), true, None::<&str>)?;
    // Clickable whether or not the key registered: the tray is the way in when the chord is taken.
    let type_to = MenuItem::with_id(app, "type", palette_label, true, None::<&str>)?;
    // After Esc or the owner's own hand paused the agent (the status line says so).
    let resume = MenuItem::with_id(app, "resume", &format!("Resume {}", worn_name()), true, None::<&str>)?;
    let this_computer = MenuItem::with_id(app, "desktop", "This computer…", true, None::<&str>)?;
    let pair = MenuItem::with_id(app, "pair", "Pair with a house…", true, None::<&str>)?;
    let updates = MenuItem::with_id(app, "updates", "Check for updates", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", &format!("Quit {}", worn_name()), true, None::<&str>)?;
    // Only said when the owner set a key. A line to read, not a button: a hold has no click.
    let dictate = dictation_label.map(|label| MenuItem::with_id(app, "dictate", label, false, None::<&str>)).transpose()?;
    let first_separator = PredefinedMenuItem::separator(app)?;
    let last_separator = PredefinedMenuItem::separator(app)?;
    let mut items: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> = vec![&status, &first_separator, &open, &talk, &type_to];
    if let Some(dictate) = &dictate {
        items.push(dictate);
    }
    items.extend::<[&dyn tauri::menu::IsMenuItem<tauri::Wry>; 6]>([&resume, &this_computer, &pair, &updates, &last_separator, &quit_item]);
    let menu = Menu::with_items(app, &items)?;
    let mut tray = TrayIconBuilder::with_id("jarvis")
        .tooltip(worn_name())
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_console(app),
            "talk" => push_to_talk(app, None),
            "type" => summon_to_type(app),
            "resume" => resume_agent(app),
            "desktop" => show_desktop(app, "status"),
            "pair" => show_desktop(app, "pair"),
            "updates" => {
                show_desktop(app, "updates");
                let app = app.clone();
                tauri::async_runtime::spawn(async move { check_for_update(&app).await });
            }
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_console(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    *app.state::<Shell>().tray_status.lock().unwrap_or_else(|e| e.into_inner()) = Some(status);
    Ok(())
}

pub fn quit(app: &AppHandle) {
    stop_agent(app);
    app.exit(0);
}

// --- updates -----------------------------------------------------------------

fn set_update(app: &AppHandle, status: &str, version: &str) {
    with(app, |s| s.update = UpdateInfo { status: status.to_string(), version: version.to_string() });
    changed(app);
}

/// Check the feed, and download a newer build in the background; it applies
/// itself later, when `update::ready_to_apply` says nothing would notice.
pub async fn check_for_update(app: &AppHandle) {
    if with(app, |s| s.downloaded.is_some()) {
        return;
    }
    set_update(app, "checking", "");
    let updater = match app.updater() {
        Ok(updater) => updater,
        Err(e) => return set_update(app, &format!("the updater is not available here: {e}"), ""),
    };
    match updater.check().await {
        Ok(Some(found)) => {
            let version = found.version.clone();
            set_update(app, "downloading", &version);
            match found.download(|_, _| {}, || {}).await {
                Ok(bytes) => {
                    with(app, |s| s.downloaded = Some((found, bytes, Instant::now())));
                    log(app, format!("update: {version} downloaded; it installs once {} is idle", worn_name()));
                    set_update(app, "ready", &version);
                }
                Err(e) => set_update(app, &format!("the download failed: {e}"), &version),
            }
        }
        Ok(None) => set_update(app, "current", ""),
        Err(e) => set_update(app, &format!("could not check: {e}"), ""),
    }
}

/// Once a minute: apply a downloaded build if the app has gone quiet.
pub fn apply_when_idle(app: &AppHandle) {
    let focused = app.get_webview_window("console").and_then(|w| w.is_focused().ok()).unwrap_or(false);
    let ready = with(app, |s| {
        let ago = s.downloaded.as_ref().map(|(_, _, at)| at.elapsed());
        update::ready_to_apply(&s.state, focused, ago)
    });
    if !ready {
        return;
    }
    let Some((found, bytes, _)) = with(app, |s| s.downloaded.take()) else { return };
    log(app, format!("update: installing {} and relaunching", found.version));
    stop_agent(app);
    match found.install(bytes) {
        Ok(()) => app.restart(),
        Err(e) => {
            log(app, format!("update: the install failed: {e}"));
            with(app, |s| s.quitting = false);
            start_agent(app);
            set_update(app, &format!("the install failed: {e}"), &found.version);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_worn_name_is_what_the_popup_title_leads_with() {
        assert_eq!(name_of("JARVIS · listening"), Some("JARVIS"));
        assert_eq!(name_of("AURA · needs you · pill"), Some("AURA"));
        assert_eq!(name_of("Settings"), None);
    }

    #[test]
    fn the_popup_reads_its_page_s_mood_from_the_title() {
        assert_eq!(mood_of("Jarvis"), Mood::Idle);
        assert_eq!(mood_of("Jarvis · working"), Mood::Working);
        assert_eq!(mood_of("AURA · needs you"), Mood::NeedsYou);
        assert_eq!(mood_of("Jarvis · notice"), Mood::Notice);
        assert_eq!(mood_of("Jarvis · something new"), Mood::Idle);
        assert_eq!(mood_of("working"), Mood::Idle);
        assert_eq!(mood_of("Jarvis · working · pill"), Mood::Working);
        assert_eq!(mood_of("Jarvis · pill"), Mood::Idle);
        // The dictation answer rides in the same title (T-1258) and must not read as a mood.
        assert_eq!(mood_of("Jarvis · typed:d1:hello%20world · pill"), Mood::Idle);
        assert_eq!(popup_size("Jarvis · typed:d1:hello · pill"), states::ISLAND_PILL_WINDOW);
        assert!(is_away("Jarvis · typed:d1: · away"));
    }

    #[test]
    fn the_popup_window_shrinks_round_the_island_s_pill_only() {
        assert_eq!(popup_size("Jarvis · working · pill"), states::ISLAND_PILL_WINDOW);
        assert_eq!(popup_size("Jarvis · pill"), states::ISLAND_PILL_WINDOW);
        assert_eq!(popup_size("Jarvis · needs you"), POPUP_SIZE);
        assert_eq!(popup_size("Jarvis"), POPUP_SIZE);
        assert_eq!(popup_size("pill"), POPUP_SIZE);
        let (w, h) = states::ISLAND_PILL_WINDOW;
        assert!(w < POPUP_SIZE.0 && h < POPUP_SIZE.1);
    }

    #[test]
    fn the_palette_key_parses_as_a_shortcut_and_differs_from_push_to_talk() {
        let palette_key = palette::palette_key(None).parse::<Shortcut>().unwrap();
        let talk = DEFAULT_PUSH_TO_TALK_KEY.parse::<Shortcut>().unwrap();
        assert_ne!(palette_key, talk);
        // Case and order of modifiers do not make another key: the clash check compares these.
        assert_eq!(palette_key, "shift+control+k".parse::<Shortcut>().unwrap());
        assert!("not a key".parse::<Shortcut>().is_err());
    }

    #[test]
    fn the_dictation_key_is_a_modified_chord_no_other_key_of_the_apps_already_is() {
        let talk = DEFAULT_PUSH_TO_TALK_KEY.parse::<Shortcut>().ok();
        let palette_key = palette::palette_key(None).parse::<Shortcut>().ok();
        let others = [(talk, "push-to-talk"), (palette_key, "typing")];
        assert!(dictation_shortcut("Ctrl+Alt+D", &others).is_ok());
        assert!(dictation_shortcut("F9", &others).unwrap_err().contains("modifier"));
        assert!(dictation_shortcut("Ctrl+Alt+", &others).is_err());
        assert!(dictation_shortcut("Ctrl+Alt+not a key", &others).unwrap_err().contains("not a key"));
        // Case and modifier order do not make another key.
        assert!(dictation_shortcut("control+shift+k", &others).unwrap_err().contains("typing"));
    }

    #[test]
    fn the_dictation_page_is_told_in_a_script_the_popup_listens_for() {
        assert!(dictation::arm_script(true, "d1").contains("jarvis:dictate"));
    }

    #[test]
    fn the_palette_summon_is_a_script_the_popup_page_listens_for() {
        assert!(palette::SUMMON_TO_TYPE_SCRIPT.starts_with("window.dispatchEvent("));
        assert!(palette::SUMMON_TO_TYPE_SCRIPT.contains("jarvis:summon"));
    }

    #[test]
    fn the_palette_page_putting_the_island_away_hides_the_window() {
        assert!(is_away("Jarvis · away"));
        assert!(!is_away("Jarvis"));
        assert!(!is_away("away"));
        assert!(!is_away("Jarvis · working · pill"));
        assert_eq!(mood_of("Jarvis · away"), Mood::Idle);
    }

    #[test]
    fn the_popup_is_the_console_with_its_assist_flag() {
        assert_eq!(popup_url("http://100.64.0.51:8199").unwrap().as_str(), "http://100.64.0.51:8199/?popup=1");
        assert_eq!(popup_url("https://jarvis.example.ts.net/").unwrap().as_str(), "https://jarvis.example.ts.net/?popup=1");
        assert!(popup_url("not a url").is_none());
    }
}
