fn main() {
    // The app's commands are listed, so each needs a permission and only the
    // windows `capabilities/local.json` names are granted them.
    let commands = &[
        "shell_state",
        "pair",
        "pending_ask",
        "answer",
        "agent_cli",
        "restart_agent",
        "agent_log",
        "set_autostart",
        "check_update",
        "open_console",
        "open_desktop",
    ];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(commands)))
        .expect("tauri-build failed");
}
