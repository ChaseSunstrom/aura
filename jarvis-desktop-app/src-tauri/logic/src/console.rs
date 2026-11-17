//! What the main window may load, and how a pasted address or a `jarvis://pair`
//! link becomes one (ADR-0051). Ported from the Electron shell's `config.ts` and
//! `pairing.ts`, with their test cases.
//!
//! The main window is a browser pointed at whatever this says, and the string
//! comes from a paste, a link somebody clicked or the environment. Loopback, the
//! tailnet and the private ranges are addresses a person chose; anything else is
//! a mistake or an attack, and the app refuses rather than rendering it.

use std::fs;
use std::path::Path;

use url::{Host, Url};

/// The console's own port, the one `docker-compose.yml` publishes.
pub const CONSOLE_PORT: u16 = 8199;
pub const DEFAULT_CONSOLE: &str = "http://127.0.0.1:8199";
/// The chosen console, kept in the app's config directory.
pub const CONSOLE_FILE: &str = "console.json";

/// Whether the main window may load `url`: http(s) on loopback, `*.ts.net`,
/// the tailnet's 100.64.0.0/10, or a private IPv4 range.
pub fn is_allowed_console(url: &str) -> bool {
    let Ok(parsed) = Url::parse(url) else { return false };
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return false;
    }
    match parsed.host() {
        Some(Host::Domain(name)) => name == "localhost" || name.ends_with(".ts.net"),
        Some(Host::Ipv4(ip)) => {
            let [a, b, ..] = ip.octets();
            ip.is_loopback()
                || (a == 100 && (64..=127).contains(&b))
                || a == 10
                || (a == 192 && b == 168)
                || (a == 172 && (16..=31).contains(&b))
        }
        Some(Host::Ipv6(ip)) => ip.is_loopback(),
        None => false,
    }
}

/// What a paste (or a clicked link) was.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pasted {
    /// The console's address: the window loads it and nothing is paired.
    Console { console_url: String },
    /// The console's pairing link: the agent pairs with `house`, the window loads
    /// the console on the house's host.
    Link { console_url: String, link: String, house: String },
    Refused(String),
}

/// Where the console answers for `scheme` on `host`. The console itself is
/// plain HTTP on [CONSOLE_PORT]; HTTPS is a TLS front before it (tailscale
/// serve on 443), so an `https` address with no port -- or with the console's
/// port, which no front listens on -- is the front's default port. Adding
/// 8199 to `https://<name>.ts.net` sent TLS to a plain-HTTP socket and the
/// window showed ERR_SSL_PROTOCOL_ERROR (2026-09-25). Does not cover a front
/// on some other port given without one; paste that port.
fn console_origin(scheme: &str, host: &str, port: Option<u16>) -> String {
    match (scheme, port) {
        ("https", None) | ("https", Some(CONSOLE_PORT)) => format!("https://{host}"),
        (_, None) => format!("{scheme}://{host}:{CONSOLE_PORT}"),
        (_, Some(port)) => format!("{scheme}://{host}:{port}"),
    }
}

fn refused(why: impl Into<String>) -> Pasted {
    Pasted::Refused(why.into())
}

fn has_scheme(raw: &str) -> bool {
    match raw.find("://") {
        Some(at) => at > 0 && raw[..at].chars().all(|c| c.is_ascii_alphabetic()),
        None => false,
    }
}

/// Read what was pasted: a `jarvis://pair?v=1&u=<house>&c=<code>` link, or the
/// console's address with or without a scheme and port.
pub fn read_pasted(text: &str) -> Pasted {
    let raw = text.trim();
    if raw.is_empty() {
        return refused("paste the console's address or its pairing link");
    }
    let lower = raw.to_ascii_lowercase();
    if lower.starts_with("jarvis://") {
        let Ok(link) = Url::parse(raw) else { return refused("that pairing link could not be read") };
        if !lower.starts_with("jarvis://pair") {
            return refused("that is a jarvis:// link, but not a pairing one");
        }
        let param = |key: &str| link.query_pairs().find(|(k, _)| k == key).map(|(_, v)| v.into_owned());
        let version = param("v").unwrap_or_else(|| "1".into());
        if version != "1" {
            return refused(format!("pairing link version {version} is not one this app reads"));
        }
        let house = param("u").unwrap_or_default().trim().to_string();
        let code: String = param("c").unwrap_or_default().chars().filter(|c| !c.is_whitespace()).collect();
        if house.is_empty() || code.is_empty() {
            return refused("the pairing link has no house or no code");
        }
        let Ok(house_url) = Url::parse(&house) else {
            return refused("the pairing link's house address could not be read");
        };
        let host = house_url.host_str().unwrap_or_default().to_string();
        // The house's own port is not the console's; only its scheme carries over.
        let console_url = console_origin(house_url.scheme(), &host, None);
        if !is_allowed_console(&console_url) {
            return refused(format!("{host} is not loopback, the tailnet or a private network"));
        }
        return Pasted::Link { console_url, link: raw.to_string(), house };
    }

    let candidate = if has_scheme(raw) { raw.to_string() } else { format!("http://{raw}") };
    let Ok(url) = Url::parse(&candidate) else { return refused("that address could not be read") };
    let Some(host) = url.host_str().map(str::to_string) else { return refused("that address could not be read") };
    let console_url = console_origin(url.scheme(), &host, url.port());
    if !is_allowed_console(&console_url) {
        return refused(format!("{host} is not loopback, the tailnet or a private network"));
    }
    Pasted::Console { console_url }
}

/// The agent's own `pair` for a link; nothing for a bare address.
pub fn agent_pair_args(pasted: &Pasted, device_name: &str) -> Option<Vec<String>> {
    match pasted {
        Pasted::Link { link, .. } => {
            Some(vec!["pair".into(), link.clone(), "--name".into(), device_name.into()])
        }
        _ => None,
    }
}

/// The console kept in `dir`, if it is one the window may load.
pub fn saved_console(dir: &Path) -> Option<String> {
    let text = fs::read_to_string(dir.join(CONSOLE_FILE)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let kept = value.get("consoleUrl")?.as_str()?.trim().to_string();
    // Read through the same rule, so a console kept by a build that wrote
    // `https://…:8199` loads the front instead of failing on every start.
    let parsed = Url::parse(&kept).ok()?;
    let url = console_origin(parsed.scheme(), parsed.host_str()?, parsed.port());
    is_allowed_console(&url).then_some(url)
}

/// Keep `console_url` in `dir`, readable only by this user.
pub fn save_console(dir: &Path, console_url: &str) -> std::io::Result<()> {
    if !is_allowed_console(console_url) {
        return Err(std::io::Error::other(format!("refusing to save {console_url}")));
    }
    fs::create_dir_all(dir)?;
    let path = dir.join(CONSOLE_FILE);
    fs::write(&path, serde_json::json!({ "consoleUrl": console_url }).to_string() + "\n")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// The environment's console, else the kept one, else loopback; and whether one
/// was ever chosen (unchosen is the first open, which gets the pairing page).
pub fn choose_console(env_url: Option<&str>, saved: Option<String>) -> (String, bool) {
    if let Some(wanted) = env_url.map(str::trim).filter(|u| !u.is_empty()) {
        if is_allowed_console(wanted) {
            return (wanted.to_string(), true);
        }
    }
    match saved {
        Some(url) => (url, true),
        None => (DEFAULT_CONSOLE.to_string(), false),
    }
}

/// `scheme://host:port` of a URL, for "is this navigation still the console".
pub fn origin_of(url: &Url) -> String {
    format!("{}://{}:{}", url.scheme(), url.host_str().unwrap_or_default(), url.port_or_known_default().unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    const LINK: &str = "jarvis://pair?v=1&u=http%3A%2F%2F100.64.0.51%3A8080&c=abc%20def";

    #[test]
    fn accepts_loopback_the_tailnet_and_the_lan() {
        for url in [
            "http://127.0.0.1:8199",
            "http://localhost:8199",
            "http://[::1]:8199",
            "https://jarvis.example.ts.net",
            "http://100.64.0.51:8199",
            "http://10.0.0.2",
            "http://192.168.1.20:8199",
            "http://172.20.0.1",
        ] {
            assert!(is_allowed_console(url), "{url}");
        }
    }

    #[test]
    fn refuses_the_internet_and_anything_not_http() {
        for url in ["https://example.com", "http://8.8.8.8", "http://100.200.0.1", "http://ts.net", "file:///etc/passwd", "javascript:alert(1)", "not a url"] {
            assert!(!is_allowed_console(url), "{url}");
        }
    }

    #[test]
    fn reads_the_consoles_address_with_its_port_when_none_is_given() {
        let console = |u: &str| Pasted::Console { console_url: u.into() };
        assert_eq!(read_pasted("http://jarvis.example.ts.net:8199"), console("http://jarvis.example.ts.net:8199"));
        assert_eq!(read_pasted("  jarvis.example.ts.net "), console("http://jarvis.example.ts.net:8199"));
        assert_eq!(read_pasted("https://100.64.0.51"), console("https://100.64.0.51"));
        // The console is plain HTTP on 8199; HTTPS is tailscale serve's front on 443.
        assert_eq!(read_pasted("https://jarvis.example.ts.net:8199"), console("https://jarvis.example.ts.net"));
        assert_eq!(read_pasted("https://jarvis.example.ts.net:8443"), console("https://jarvis.example.ts.net:8443"));
        assert_eq!(read_pasted("192.168.1.20:8080"), console("http://192.168.1.20:8080"));
    }

    #[test]
    fn reads_the_pairing_link_the_house_host_at_the_consoles_port() {
        let read = read_pasted(LINK);
        assert_eq!(
            read,
            Pasted::Link {
                console_url: "http://100.64.0.51:8199".into(),
                link: LINK.into(),
                house: "http://100.64.0.51:8080".into(),
            }
        );
        assert_eq!(agent_pair_args(&read, "study-pc").unwrap(), ["pair", LINK, "--name", "study-pc"]);
        // A house behind tailscale serve: the console is the front on 443, never :8199 over TLS.
        let tls = "jarvis://pair?v=1&u=https%3A%2F%2Fjarvis.example.ts.net&c=abc";
        match read_pasted(tls) {
            Pasted::Link { console_url, .. } => assert_eq!(console_url, "https://jarvis.example.ts.net"),
            other => panic!("{other:?}"),
        }
        assert_eq!(agent_pair_args(&read_pasted("http://127.0.0.1:8199"), "x"), None);
    }

    #[test]
    fn refuses_what_the_window_must_not_load() {
        for text in [
            "",
            "https://example.com",
            "jarvis://pair?v=2&u=http%3A%2F%2F10.0.0.2%3A8080&c=abc",
            "jarvis://pair?v=1&u=http%3A%2F%2F10.0.0.2%3A8080",
            "jarvis://pair?v=1&u=http%3A%2F%2Fevil.example%3A8080&c=abc",
            "jarvis://other?v=1",
            "not a url at all ::",
        ] {
            assert!(matches!(read_pasted(text), Pasted::Refused(_)), "{text:?}");
        }
    }

    #[test]
    fn the_kept_console_is_0600_read_back_and_ignored_when_not_loadable() {
        let dir = std::env::temp_dir().join(format!("jarvis-console-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        assert_eq!(saved_console(&dir), None);
        save_console(&dir, "http://100.64.0.51:8199").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(dir.join(CONSOLE_FILE)).unwrap().permissions().mode();
            assert_eq!(mode & 0o077, 0);
        }
        assert_eq!(saved_console(&dir).as_deref(), Some("http://100.64.0.51:8199"));
        assert!(save_console(&dir, "https://example.com").is_err());
        fs::write(dir.join(CONSOLE_FILE), r#"{"consoleUrl": "https://example.com"}"#).unwrap();
        assert_eq!(saved_console(&dir), None);
        // What the first build kept for a tailnet-name link: repaired on read.
        fs::write(dir.join(CONSOLE_FILE), r#"{"consoleUrl": "https://jarvis.example.ts.net:8199"}"#).unwrap();
        assert_eq!(saved_console(&dir).as_deref(), Some("https://jarvis.example.ts.net"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_console_is_the_environments_then_the_kept_one_then_loopback() {
        assert_eq!(choose_console(None, None), (DEFAULT_CONSOLE.to_string(), false));
        let kept = Some("http://10.0.0.5:8199".to_string());
        assert_eq!(choose_console(None, kept.clone()), ("http://10.0.0.5:8199".into(), true));
        assert_eq!(choose_console(Some("http://127.0.0.1:8199"), kept.clone()), ("http://127.0.0.1:8199".into(), true));
        assert_eq!(choose_console(Some("https://example.com"), kept), ("http://10.0.0.5:8199".into(), true));
    }
}
