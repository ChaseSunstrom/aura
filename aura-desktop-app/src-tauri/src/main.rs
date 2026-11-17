//! The Jarvis desktop app (ADR-0051): the console in a window, a tray that says
//! what the assistant is doing, a push-to-talk key, native approval prompts, the
//! `jarvis://` pairing link, and the device agent -- the Python agent, frozen
//! into a sidecar the app starts and watches -- in one binary per platform.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod shell;

use jarvis_desktop_logic::{console, update};

use tauri::{Manager, RunEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// Started at login: tray only, no window.
const HIDDEN: &str = "--hidden";

fn open_link(app: &tauri::AppHandle, url: String) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        shell::pair(&app, &url).await;
        shell::show_desktop(&app, "pair");
    });
}

fn env_flag(name: &str, fallback: bool) -> bool {
    match std::env::var(name).map(|v| v.trim().to_ascii_lowercase()) {
        Ok(v) if ["0", "false", "no", "off"].contains(&v.as_str()) => false,
        Ok(v) if ["1", "true", "yes", "on"].contains(&v.as_str()) => true,
        _ => fallback,
    }
}

fn main() {
    // Env names say AURA_ since T-0959, but a desktop's own environment may still
    // say JARVIS_: each JARVIS_ name is read under its AURA_ name unless that is set. Before any
    // thread exists, like the WebKit switch below; the agent this app starts
    // inherits the adopted names.
    for (name, value) in std::env::vars_os() {
        if let Some(rest) = name.to_str().and_then(|n| n.strip_prefix("JARVIS_")) {
            let aura = format!("AURA_{rest}");
            if std::env::var_os(&aura).is_none() {
                std::env::set_var(aura, value);
            }
        }
    }

    // WebKitGTK's DMA-BUF renderer aborts at start on a host whose EGL it cannot
    // open -- NVIDIA drivers, some Wayland compositors -- with the AppImage's
    // bundled libraries against a newer system (the owner, 1.0.409 on Arch:
    // SIGABRT in the first second, no window). Off unless the person set it,
    // before GTK starts and before any thread exists; the cost is WebKit's
    // slower shared-memory path, which the console's panels do not notice.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    let push_to_talk_key = std::env::var("AURA_PUSH_TO_TALK_KEY")
        .ok()
        .filter(|k| !k.trim().is_empty())
        .unwrap_or_else(|| shell::DEFAULT_PUSH_TO_TALK_KEY.to_string());

    // The typing key (T-0696): the same island, summoned to its line. Registered once the app
    // has a handle; here only so the plugin's handler can tell it from push-to-talk.
    let palette_key = jarvis_desktop_logic::palette::palette_key(std::env::var("AURA_PALETTE_KEY").ok().as_deref());
    let push_to_talk_shortcut = push_to_talk_key.parse::<Shortcut>().ok();
    let palette_shortcut = palette_key.parse::<Shortcut>().ok().filter(|p| push_to_talk_shortcut != Some(*p));

    // The hold-to-dictate key (T-1258): off until the owner sets it. A chord with no modifier, or
    // one another key of this app's already is, is not registered and the handler never sees it.
    let dictation_key = std::env::var("AURA_DICTATION_KEY").ok().map(|k| k.trim().to_string()).filter(|k| !k.is_empty());
    let other_keys = [
        (push_to_talk_shortcut, "push-to-talk"),
        (palette_shortcut, "typing"),
        (Some(shell::escape_key()), "Esc"),
    ];
    let dictation_shortcut = dictation_key.as_deref().and_then(|k| shell::dictation_shortcut(k, &other_keys).ok());

    let app = tauri::Builder::default()
        // First, so a second launch -- including one started by a clicked
        // `jarvis://` link -- hands its arguments to this one and exits.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if !argv.iter().any(|a| a.starts_with("jarvis://")) {
                shell::show_console(app);
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec![HIDDEN])))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    let pressed = event.state() == ShortcutState::Pressed;
                    if *shortcut == shell::escape_key() {
                        // Held only while the overlay is up (T-0617): the owner taking over.
                        if pressed {
                            shell::owner_escape(app);
                        }
                    } else if Some(*shortcut) == dictation_shortcut {
                        // Held to dictate: down records, up transcribes and types into the focused window.
                        shell::dictate_key(app, pressed);
                    } else if Some(*shortcut) == palette_shortcut {
                        // A press, not a hold: the island comes up with its line focused.
                        if pressed {
                            shell::summon_to_type(app);
                        }
                    } else {
                        // Held to talk: down starts the turn, up ends it.
                        shell::push_to_talk(app, Some(pressed));
                    }
                })
                .build(),
        )
        .manage(shell::Shell::default())
        .invoke_handler(tauri::generate_handler![
            commands::shell_state,
            commands::pair,
            commands::pending_ask,
            commands::answer,
            commands::agent_cli,
            commands::restart_agent,
            commands::agent_log,
            commands::set_autostart,
            commands::check_update,
            commands::open_console,
            commands::open_desktop,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let env_console = std::env::var("AURA_CONSOLE_URL").ok();
            let (url, chosen) = console::choose_console(env_console.as_deref(), console::saved_console(&shell::config_dir(&handle)));
            {
                let state = app.state::<shell::Shell>();
                let mut inner = state.inner.lock().unwrap_or_else(|e| e.into_inner());
                inner.console_url = url;
                inner.console_chosen = chosen;
                inner.state = "offline".into();
                inner.update.status = "idle".into();
            }
            match push_to_talk_key.parse::<Shortcut>() {
                Ok(shortcut) => {
                    if let Err(e) = app.global_shortcut().register(shortcut) {
                        // Another app owns it; said out loud, because the
                        // alternative is a key that silently does nothing.
                        shell::log(&handle, format!("could not register {push_to_talk_key}: {e}"));
                    }
                }
                Err(e) => shell::log(&handle, format!("{push_to_talk_key} is not a key this app can register: {e}")),
            }
            // After push-to-talk, so a clash with it is the typing key's to lose; before the tray,
            // which says whether the chord is ours.
            let palette_label = shell::register_palette_key(&handle, &palette_key, push_to_talk_shortcut);
            let dictation_label =
                dictation_key.as_deref().map(|key| shell::register_dictation_key(&handle, key, &other_keys));
            shell::build_tray(&handle, &push_to_talk_key, &palette_label, dictation_label.as_deref())?;

            // `jarvis://pair?…` from the console's Pairing panel. Linux has no
            // installer to register the scheme, so the app does it on start.
            #[cfg(any(target_os = "linux", all(debug_assertions, windows)))]
            if let Err(e) = app.deep_link().register_all() {
                shell::log(&handle, format!("could not register jarvis:// links: {e}"));
            }
            let links = handle.clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    open_link(&links, url.to_string());
                }
            });

            shell::start_agent(&handle);
            shell::prepare_popup(&handle);
            shell::prepare_overlay(&handle);

            let launched_by_link = app.deep_link().get_current().ok().flatten().unwrap_or_default();
            let hidden = std::env::args().any(|a| a == HIDDEN);
            if !launched_by_link.is_empty() {
                for url in launched_by_link {
                    open_link(&handle, url.to_string());
                }
            } else if !hidden {
                if chosen {
                    shell::show_console(&handle);
                } else {
                    shell::show_desktop(&handle, "pair");
                }
            }

            // Hourly check, and a once-a-minute look for a quiet moment to apply
            // a downloaded build. `AURA_AUTO_UPDATE=0` turns the check off; the
            // Updates page's "Check now" still works.
            let updates = handle.clone();
            tauri::async_runtime::spawn(async move {
                loop {
                    if env_flag("AURA_AUTO_UPDATE", true) {
                        shell::check_for_update(&updates).await;
                    }
                    tokio::time::sleep(update::CHECK_INTERVAL).await;
                }
            });
            let applier = handle.clone();
            tauri::async_runtime::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                    shell::apply_when_idle(&applier);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("the desktop app could not start");

    app.run(|app, event| match event {
        // A tray app has no windows for most of its life: closing the last one
        // must not end it. Quitting is the tray's, which exits with a code.
        RunEvent::ExitRequested { code: None, api, .. } => api.prevent_exit(),
        RunEvent::Exit => shell::stop_agent(app),
        _ => {}
    });
}
