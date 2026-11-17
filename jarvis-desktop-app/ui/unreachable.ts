/** "No console there yet": the address tried, and the way to another. The app retries on its own. */
import { STATE_GLYPH } from './tokens.js';

const { invoke } = window.__TAURI__.core;
const url = new URLSearchParams(location.search).get('url') ?? '';
(document.getElementById('url') as HTMLElement).textContent = url;
(document.getElementById('glyph') as HTMLElement).textContent = STATE_GLYPH.offline;
document.getElementById('change')?.addEventListener('click', () => void invoke('open_desktop', { section: 'pair' }));
