// @generated from themes/<id>/theme.json by design/build.py — DO NOT EDIT.
// Every theme pack as data: what the token vocabulary has no name for (persona, voice, wake
// phrases, core renderer and states, map style, marker, toggle). The colours, fonts, radii and
// motion are already in themes.css under :root[data-theme=…].

export type ThemeId = 'aura' | 'jarvis';
export type CoreState = 'idle' | 'listen' | 'speak' | 'think' | 'embed' | 'music' | 'alert' | 'work';
export const THEME_IDS: readonly ThemeId[] = ["aura", "jarvis"] as const;
export const DEFAULT_THEME: ThemeId = 'aura';

export const THEMES = {
	"aura": {
		"id": "aura",
		"name": "AURA",
		"version": "1.0.0",
		"description": "Starlight on deep space. The core is a living spiral galaxy.",
		"persona": {
			"name": "AURA",
			"spokenName": "Aura",
			"voiceDescription": "a young woman's voice: warm, clear, natural, calm; never breathy or exaggerated",
			"style": "warm, brief, plain-spoken; light humour only when the moment is light",
			"strings": {
				"ready": "Ready when you are.",
				"listening": "Listening…",
				"thinking": "Thinking…",
				"working": "On it.",
				"offline": "I can’t reach home right now — I’ll keep what I can on this device.",
				"approvalAsk": "Needs your OK",
				"takeOver": "Esc to take over"
			}
		},
		"wake": {
			"phrases": [
				"aura",
				"hey aura"
			],
			"engine": "openwakeword",
			"model": "assets/wake/aura.onnx",
			"threshold": 0.95,
			"useSpeakerVerification": true,
			"followUpWindowMs": 6000,
			"targets": {
				"falseAcceptsPer24hSpeech": 1,
				"falseRejectRate": 0.05
			}
		},
		"voice": {
			"engine": "kokoro",
			"voiceId": "af_heart",
			"rate": 1.0,
			"pitchSemitones": 0,
			"offline": {
				"engine": "kokoro-onnx-int8",
				"voiceId": "af_heart",
				"fallback": "prerendered-clips"
			},
			"earcons": {
				"wake": "assets/sound/aura-wake.ogg",
				"done": "assets/sound/aura-done.ogg",
				"approval": "assets/sound/aura-approval.ogg",
				"error": "assets/sound/aura-error.ogg"
			}
		},
		"shape": {
			"radius": {
				"chip": 999,
				"button": 999,
				"panel": 18,
				"card": 20,
				"sheet": 22
			},
			"marker": "star",
			"toggle": "pill"
		},
		"motion": {
			"easeOut": "cubic-bezier(.2,.9,.25,1)",
			"easeIn": "cubic-bezier(.6,0,.8,.2)",
			"ms": {
				"appear": 550,
				"leave": 450,
				"stagger": 35,
				"bars": 400,
				"line": 500,
				"beat": 1200,
				"stateCross": 450
			},
			"appear": "from-core: point → line → full height, slight tilt toward the core",
			"leave": "reverse into the core",
			"reducedMotion": "fade only"
		},
		"background": {
			"type": "starfield",
			"twinkle": true,
			"nebulae": [
				{
					"color": "rgba(244,114,208,.11)",
					"at": [
						0.24,
						0.74
					],
					"size": [
						520,
						340
					]
				},
				{
					"color": "rgba(124,199,255,.08)",
					"at": [
						0.8,
						0.2
					],
					"size": [
						640,
						400
					]
				}
			]
		},
		"core": {
			"renderer": "galaxy",
			"reference": "assets/core/galaxy_engine.js",
			"params": {
				"arms": 2,
				"wind": 4.6,
				"starsDesktop": 2100,
				"starsPhone": 1200,
				"starsMini": 360,
				"nebulae": 18,
				"nebulaeMini": 6,
				"jets": 90,
				"jetsMini": 30,
				"dustLanes": 26,
				"knots": 14,
				"auraRings": [
					1.0,
					1.1
				],
				"flareMs": 650
			},
			"palette": {
				"warm": "#FFEEDC",
				"pink": "#F48CD6",
				"lavender": "#BEA0FF",
				"blue": "#82BEFF",
				"gold": "#FFCD82",
				"ice": "#E4EEFF",
				"dust": "#060310",
				"knot": "#FFAAE6",
				"knotCool": "#BED2FF",
				"spark": "#FFFAFF",
				"listenRing": "#D7C3FF",
				"alertRing": "#FF7896",
				"jet": "#EBD7FF",
				"jetSpark": "#F0E1FF",
				"auraRing": "#C8AFFF",
				"sweep": "#FAF0FF",
				"comet": "#E6D7FF",
				"cometWarm": "#FFD296",
				"glow": "#FFE1F5",
				"bulge": "#FFFDFF",
				"spike": "#FFF5FF",
				"accretion": "#FFD7F5",
				"accretionHot": "#FFF5F5",
				"shock": "#E1CDFF"
			},
			"tween": 0.035,
			"states": {
				"idle": {
					"spin": 0.2,
					"tilt": 1.05,
					"radius": 1.0,
					"wind": 0.0,
					"glow": 0.85,
					"comets": 0,
					"jets": 0,
					"accretion": 0.5,
					"motion": "slow turn, breathing"
				},
				"listen": {
					"spin": 0.28,
					"tilt": 0.42,
					"radius": 1.02,
					"wind": 0.0,
					"glow": 1.0,
					"comets": 0,
					"jets": 0,
					"accretion": 0.7,
					"motion": "faces you; rings roll outward with mic level"
				},
				"speak": {
					"spin": 0.32,
					"tilt": 0.62,
					"radius": 1.02,
					"wind": 0.0,
					"glow": 1.15,
					"comets": 0,
					"jets": 0,
					"accretion": 0.8,
					"motion": "core pulses with TTS envelope; light runs out along the arms"
				},
				"think": {
					"spin": 1.3,
					"tilt": 1.12,
					"radius": 0.86,
					"wind": 1.3,
					"glow": 0.9,
					"comets": 0.3,
					"jets": 1,
					"accretion": 1.0,
					"tint": "#CD96FF",
					"tintAmount": 0.35,
					"motion": "arms wind tight; polar jets"
				},
				"embed": {
					"spin": 0.7,
					"tilt": 0.75,
					"radius": 0.95,
					"wind": 0.4,
					"glow": 1.05,
					"comets": 0,
					"jets": 0.25,
					"accretion": 1.3,
					"tint": "#F48CD6",
					"tintAmount": 0.25,
					"motion": "stars spiral into the core"
				},
				"music": {
					"spin": 0.42,
					"tilt": 0.55,
					"radius": 1.03,
					"wind": 0.0,
					"glow": 1.05,
					"comets": 0,
					"jets": 0,
					"accretion": 0.8,
					"motion": "waves roll outward on the beat"
				},
				"alert": {
					"spin": 0.06,
					"tilt": 0.3,
					"radius": 0.9,
					"wind": 0.0,
					"glow": 0.9,
					"comets": 0,
					"jets": 0,
					"accretion": 0.6,
					"tint": "#FF6E8C",
					"tintAmount": 0.6,
					"motion": "faces you; rose rings close inward"
				},
				"work": {
					"spin": 1.05,
					"tilt": 0.82,
					"radius": 0.93,
					"wind": 0.2,
					"glow": 1.0,
					"comets": 1,
					"jets": 0.3,
					"accretion": 0.9,
					"motion": "comets orbit; shock ring every beat"
				}
			},
			"onStateChange": "flash + shock ring"
		},
		"map": {
			"style": "space-drive",
			"sky": {
				"gradient": [
					"#05030D",
					"#120828",
					"#3A1A63"
				],
				"band": "milky-way",
				"stars": 320
			},
			"ground": {
				"gradient": [
					"#1C0B3A",
					"#0B0519",
					"#05030D"
				],
				"texture": "star-dust"
			},
			"road": {
				"edges": "dotted-light",
				"surface": "star-stream",
				"lanes": "#F2EAFF@35%"
			},
			"buildings": {
				"style": "crystal",
				"near": "#B79CFF",
				"far": "#F48CD6",
				"windows": true,
				"beacons": "#FF7896"
			},
			"route": {
				"style": "comet",
				"color": "#FFC36B"
			},
			"turnMarker": "planet",
			"otherVehicles": "orb:#FF7896",
			"ego": "starlight-wireframe+aura+wake",
			"labels": {
				"font": "display",
				"case": "upper",
				"backing": "pill"
			},
			"coreDock": "bottom-right"
		},
		"haptics": {
			"wake": "soft-tick",
			"done": "double-tick",
			"approval": "firm"
		},
		"typography": {
			"display": {
				"family": "Sora",
				"weights": [
					200,
					300,
					400,
					500
				],
				"fallback": [
					"system-ui",
					"sans-serif"
				],
				"faces": [
					"sora-200",
					"sora-300",
					"sora-400",
					"sora-500"
				]
			},
			"mono": {
				"family": "DM Mono",
				"weights": [
					300,
					400,
					500
				],
				"fallback": [
					"ui-monospace",
					"monospace"
				],
				"faces": [
					"dm-mono-300",
					"dm-mono-400",
					"dm-mono-500"
				]
			},
			"roles": {
				"hero": {
					"font": "display",
					"size": 52,
					"weight": 200
				},
				"title": {
					"font": "display",
					"size": 26,
					"weight": 300
				},
				"say": {
					"font": "display",
					"size": 21,
					"weight": 300
				},
				"body": {
					"font": "mono",
					"size": 13,
					"weight": 400
				},
				"label": {
					"font": "mono",
					"size": 10.5,
					"weight": 400,
					"case": "upper",
					"tracking": 0.22,
					"fill": "gradient:accent→memory"
				},
				"data": {
					"font": "mono",
					"size": 12,
					"weight": 400,
					"numeric": "tabular"
				}
			},
			"car": {
				"scale": 1.25,
				"minSize": 15
			}
		},
		"color": {
			"bg.void": "#05030D",
			"bg.nebula": "#2A1250",
			"bg.deep": "#120828",
			"accent": "#B79CFF",
			"accent.soft": "#E4D7FF",
			"accent.gradient": [
				"#D9C7FF",
				"#B79CFF",
				"#F2A4E2"
			],
			"memory": "#F472D0",
			"cool": "#7CC7FF",
			"attention": "#FFC36B",
			"attention.soft": "#FFD89A",
			"ok": "#6EE7B7",
			"danger": "#FF6B8B",
			"text": "#F7F2FF",
			"text.secondary": "#C8BCE3",
			"text.label": "#AFA1D6",
			"off": "#5E5288",
			"on.accent": "#170A2E",
			"on.attention": "#2A1A05",
			"panel.fill": [
				"rgba(183,156,255,.08)",
				"rgba(10,5,24,0)"
			],
			"panel.edge": "rgba(236,220,255,.14)",
			"glow.text": "rgba(183,156,255,.45)"
		},
		"icons": {
			"app": "assets/icons/aura-icon-1024.png",
			"sizes": {
				"16": "assets/icons/aura-icon-16.png",
				"32": "assets/icons/aura-icon-32.png",
				"48": "assets/icons/aura-icon-48.png",
				"64": "assets/icons/aura-icon-64.png",
				"128": "assets/icons/aura-icon-128.png",
				"152": "assets/icons/aura-icon-152.png",
				"167": "assets/icons/aura-icon-167.png",
				"180": "assets/icons/aura-icon-180.png",
				"192": "assets/icons/aura-icon-192.png",
				"256": "assets/icons/aura-icon-256.png",
				"512": "assets/icons/aura-icon-512.png",
				"1024": "assets/icons/aura-icon-1024.png"
			},
			"maskable": {
				"192": "assets/icons/aura-icon-maskable-192.png",
				"512": "assets/icons/aura-icon-maskable-512.png"
			},
			"androidAdaptive": {
				"foreground": "assets/icons/aura-adaptive-foreground-432.png",
				"background": "assets/icons/aura-adaptive-background-432.png"
			},
			"monochrome": "assets/icons/aura-glyph.svg",
			"monochromePng": {
				"white24": "assets/icons/aura-glyph-white-24.png",
				"white48": "assets/icons/aura-glyph-white-48.png",
				"white96": "assets/icons/aura-glyph-white-96.png",
				"black96": "assets/icons/aura-glyph-black-96.png"
			},
			"favicon": "assets/icons/favicon.ico"
		}
	},
	"jarvis": {
		"id": "jarvis",
		"name": "JARVIS",
		"version": "1.0.0",
		"description": "Projected cyan light; the core is a 3D reactor. Kept as a second theme to prove the system.",
		"persona": {
			"name": "JARVIS",
			"spokenName": "Jarvis",
			"voiceDescription": "a calm, dry British man's voice",
			"style": "precise, understated, dry wit",
			"strings": {
				"ready": "Ready when you are.",
				"listening": "Listening…",
				"thinking": "Thinking…",
				"working": "On it.",
				"offline": "I can’t reach the server — working locally.",
				"approvalAsk": "Needs your OK",
				"takeOver": "Esc to take over"
			}
		},
		"wake": {
			"phrases": [
				"jarvis",
				"hey jarvis"
			],
			"engine": "openwakeword",
			"model": "builtin:hey_jarvis_v0.1",
			"threshold": 0.6,
			"useSpeakerVerification": true,
			"followUpWindowMs": 6000
		},
		"voice": {
			"engine": "kokoro",
			"voiceId": "bm_george",
			"rate": 1.0,
			"pitchSemitones": 0,
			"offline": {
				"engine": "kokoro-onnx-int8",
				"voiceId": "bm_george",
				"fallback": "bundled-voice"
			}
		},
		"shape": {
			"radius": {
				"chip": 999,
				"button": 2,
				"panel": 3,
				"card": 4,
				"sheet": 4
			},
			"marker": "bracket",
			"toggle": "square"
		},
		"motion": {
			"easeOut": "cubic-bezier(.2,.9,.25,1)",
			"easeIn": "cubic-bezier(.6,0,.8,.2)",
			"ms": {
				"appear": 500,
				"leave": 450,
				"stagger": 35,
				"bars": 400,
				"line": 500,
				"beat": 1200,
				"stateCross": 450
			},
			"appear": "from-core",
			"leave": "reverse into the core",
			"reducedMotion": "fade only"
		},
		"background": {
			"type": "radial",
			"nebulae": []
		},
		"core": {
			"renderer": "reactor",
			"params": {
				"shellPoints": 1500,
				"rings": [
					{
						"r": 0.56,
						"segments": 12
					},
					{
						"r": 0.46,
						"segments": 10
					},
					{
						"r": 0.36,
						"segments": 8
					}
				]
			},
			"tween": 0.035,
			"states": {
				"idle": {},
				"listen": {},
				"speak": {},
				"think": {},
				"embed": {},
				"music": {},
				"alert": {},
				"work": {}
			},
			"onStateChange": "shock ring"
		},
		"map": {
			"style": "hologram",
			"buildings": "wireframe",
			"route": "ribbon",
			"turnMarker": "chevron",
			"otherVehicles": "box:#FFB547",
			"ego": "wireframe",
			"coreDock": "bottom-right"
		},
		"typography": {
			"display": {
				"family": "Space Grotesk",
				"weights": [
					300,
					400,
					500
				],
				"fallback": [
					"system-ui",
					"-apple-system",
					"Segoe UI",
					"Roboto",
					"sans-serif"
				],
				"faces": [
					"space-grotesk-300",
					"space-grotesk-400"
				]
			},
			"mono": {
				"family": "JetBrains Mono",
				"weights": [
					400,
					500
				],
				"fallback": [
					"SFMono-Regular",
					"ui-monospace",
					"Menlo",
					"Consolas",
					"monospace"
				],
				"faces": [
					"jetbrains-mono-400",
					"jetbrains-mono-500",
					"jetbrains-mono-600"
				]
			},
			"roles": {
				"hero": {
					"font": "display",
					"size": 52,
					"weight": 300
				},
				"title": {
					"font": "display",
					"size": 26,
					"weight": 300
				},
				"say": {
					"font": "display",
					"size": 21,
					"weight": 300
				},
				"body": {
					"font": "mono",
					"size": 13,
					"weight": 400
				},
				"label": {
					"font": "mono",
					"size": 10,
					"weight": 400,
					"case": "upper",
					"tracking": 0.22,
					"fill": "color:text.label"
				},
				"data": {
					"font": "mono",
					"size": 12,
					"weight": 400,
					"numeric": "tabular"
				}
			},
			"car": {
				"scale": 1.25,
				"minSize": 15
			}
		},
		"color": {
			"bg.void": "#010409",
			"bg.nebula": "#062236",
			"bg.deep": "#030A12",
			"accent": "#3FD0E8",
			"accent.soft": "#9BEAF6",
			"accent.gradient": [
				"#9BEAF6",
				"#3FD0E8",
				"#3FD0E8"
			],
			"memory": "#9B8CFF",
			"cool": "#3FD0E8",
			"attention": "#FFB547",
			"attention.soft": "#FFC97A",
			"ok": "#5FE3A1",
			"danger": "#FF6B8B",
			"text": "#F2FBFF",
			"text.secondary": "#A9C8D8",
			"text.label": "#8DB4CA",
			"off": "#4E6177",
			"on.accent": "#02050A",
			"on.attention": "#1A1206",
			"panel.fill": [
				"rgba(1,8,14,.75)",
				"rgba(1,8,14,0)"
			],
			"panel.edge": "rgba(63,208,232,.26)",
			"glow.text": "rgba(63,208,232,.45)",
			"bg.raised": "#030A12",
			"bg.panel": "#040B13",
			"surface.2": "#081A23",
			"surface.sunken": "#01040A",
			"accent.deep": "#2C8FA3",
			"line": "#0A2129",
			"line.soft": "#081920",
			"line.hair": "#07151C"
		},
		"icons": {
			"app": "assets/icons/icon-512.png",
			"sizes": {
				"180": "assets/icons/apple-touch-icon.png",
				"192": "assets/icons/icon-192.png",
				"512": "assets/icons/icon-512.png"
			},
			"monochrome": "assets/icons/favicon.svg",
			"favicon": "assets/icons/favicon.ico"
		}
	}
} as const;
