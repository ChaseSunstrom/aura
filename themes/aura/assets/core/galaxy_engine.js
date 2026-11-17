  orbEngine(cv, getMode, o) {
    o = o || {};
    const ctx = cv.getContext('2d'), W = cv.width, H = cv.height, cx = W / 2, cy = H / 2;
    const S = Math.min(W, H) * (o.scale || .36) * 1.12, sc = W / 600, mini = !!o.mini;
    const N = Math.round((o.n || 1500) * 1.4);
    let seed = 7 + (o.seed || 0);
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const gs = () => { const u = rnd() || 1e-9; return Math.sqrt(-2 * Math.log(u)) * Math.cos(6.283 * rnd()); };
    const mix = (a, b, f) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    const WARM = [255, 238, 220], PINK = [244, 140, 214], LAV = [190, 160, 255], BLUE = [130, 190, 255], GOLD = [255, 205, 130], ICE = [228, 238, 255];
    const WIND = 4.6, armTh = (arm, r) => arm * Math.PI + r * WIND;
    const ST = [];
    for (let i = 0; i < N; i++) {
      const k = rnd(); let r, th, z, c, sz, b, arm = false;
      if (k < .2) { r = Math.min(.32, Math.abs(gs()) * .1); th = rnd() * 6.283; z = gs() * .06 * (1 - r * 2.5); c = mix(WARM, PINK, r / .32); sz = .7 + rnd() * .9; b = .55 + rnd() * .45; }
      else if (k < .9) { arm = true; const a = Math.floor(rnd() * 2); r = .08 + Math.pow(rnd(), .8) * .95; th = armTh(a, r) + gs() * (.12 + .14 * r); r = Math.max(.05, r + gs() * .025); z = gs() * .022; c = r < .35 ? mix(PINK, LAV, (r - .08) / .27) : mix(LAV, BLUE, Math.min(1, (r - .35) / .6) * (rnd() < .5 ? 1 : .4)); sz = .5 + rnd() * 1.1; b = .35 + rnd() * .55; }
      else { r = .15 + rnd() * .95; th = rnd() * 6.283; z = gs() * .2; c = mix(LAV, ICE, rnd()); sz = .4 + rnd() * .6; b = .12 + rnd() * .25; }
      const hot = rnd() < .028; if (hot) { c = rnd() < .5 ? ICE : GOLD; sz *= 1.9; b = 1; }
      ST.push({ r, th, z, c: c.map(Math.round), sz, b, hot, arm, tw: rnd() < .35, ph: rnd() * 6.283, sp: .6 + rnd() * .8, life: rnd() });
    }
    const NEB = [], DUST = [], KNOT = [], JET = [];
    for (let i = 0; i < (mini ? 6 : 18); i++) { const a = i % 2, r = .2 + rnd() * .7; NEB.push({ r, th: armTh(a, r) + gs() * .15, s: .12 + rnd() * .2, c: [PINK, LAV, BLUE][Math.floor(rnd() * 3)].map(Math.round) }); }
    if (!mini) {
      if (!o.icon) for (let i = 0; i < 26; i++) { const a = i % 2, r = .14 + rnd() * .72; DUST.push({ r, th: armTh(a, r) - .2 - rnd() * .08, s: .04 + rnd() * .05 }); }
      for (let i = 0; i < 14; i++) { const a = i % 2, r = .28 + rnd() * .62; KNOT.push({ r, th: armTh(a, r) + gs() * .07, ph: rnd() * 6.283, c: rnd() < .7 ? '255,170,230' : '190,210,255' }); }
    }
    for (let i = 0; i < (mini ? 30 : 90); i++) JET.push({ u: rnd(), sgn: i % 2 ? 1 : -1, jx: gs(), jy: gs(), sp: .7 + rnd() * .6 });
    const Z = [0, 0, 0];
    const T = {
      idle: { spin: .2, tilt: 1.05, rad: 1, wind: 0, glow: .85, comet: 0, jet: 0, acc: .5, tint: Z, ta: 0 },
      listen: { spin: .28, tilt: .42, rad: 1.02, wind: 0, glow: 1, comet: 0, jet: 0, acc: .7, tint: Z, ta: 0 },
      speak: { spin: .32, tilt: .62, rad: 1.02, wind: 0, glow: 1.15, comet: 0, jet: 0, acc: .8, tint: Z, ta: 0 },
      think: { spin: 1.3, tilt: 1.12, rad: .86, wind: 1.3, glow: .9, comet: .3, jet: 1, acc: 1, tint: [205, 150, 255], ta: .35 },
      embed: { spin: .7, tilt: .75, rad: .95, wind: .4, glow: 1.05, comet: 0, jet: .25, acc: 1.3, tint: [244, 140, 214], ta: .25 },
      music: { spin: .42, tilt: .55, rad: 1.03, wind: 0, glow: 1.05, comet: 0, jet: 0, acc: .8, tint: Z, ta: 0 },
      alert: { spin: .06, tilt: .3, rad: .9, wind: 0, glow: .9, comet: 0, jet: 0, acc: .6, tint: [255, 110, 140], ta: .6 },
      act: { spin: .95, tilt: .9, rad: .95, wind: .3, glow: 1, comet: 1, jet: .3, acc: .9, tint: Z, ta: 0 },
      work: { spin: 1.05, tilt: .82, rad: .93, wind: .2, glow: 1, comet: 1, jet: .3, acc: .9, tint: Z, ta: 0 }
    };
    const cur = { spin: .2, tilt: 1.05, rad: 1, wind: 0, glow: .85, comet: 0, jet: 0, acc: .5, tint: [0, 0, 0], ta: 0 };
    let last = 0, rotA = 0, lastMode = '', kick = -1e9;
    const self = this, key = o.key || 'orb';
    const line = (a, b, col, w) => { ctx.strokeStyle = col; ctx.lineWidth = w * sc; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
    const blob = (q, rr, inner, outer) => { const g = ctx.createRadialGradient(q[0], q[1], 0, q[0], q[1], rr); g.addColorStop(0, inner); g.addColorStop(1, outer); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(q[0], q[1], rr, 0, 6.283); ctx.fill(); };
    const frame = (t) => {
      if (!cv.isConnected) return;
      const dt = Math.min(50, t - (last || t)); last = t;
      const mode = getMode(), tg = T[mode] || T.idle;
      if (mode !== lastMode) { if (lastMode) kick = t; lastMode = mode; }
      if (mode === 'work' && t - kick > 1200) kick = t;
      for (const k of ['spin', 'tilt', 'rad', 'wind', 'glow', 'comet', 'jet', 'acc', 'ta']) cur[k] += (tg[k] - cur[k]) * .035;
      for (let i = 0; i < 3; i++) cur.tint[i] += ((tg.ta > 0 ? tg.tint[i] : cur.tint[i]) - cur.tint[i]) * .05;
      rotA += dt * .001 * cur.spin;
      const sk = t - kick, flare = sk < 650 ? .55 * (1 - sk / 650) : 0;
      const beat = .5 + .5 * Math.sin(t * .008) * Math.sin(t * .0031);
      const amp = mode === 'listen' ? (.5 + .5 * Math.abs(Math.sin(t * .006) * Math.sin(t * .0017))) : mode === 'speak' ? beat : mode === 'music' ? Math.pow(Math.abs(Math.sin(t * .0065)), 3) : 0;
      const breath = 1 + .012 * Math.sin(t * .0015), radK = cur.rad * breath;
      const ct = Math.cos(cur.tilt), st = Math.sin(cur.tilt), pa = -.32 + .04 * Math.sin(t * .00011), cp = Math.cos(pa), sp = Math.sin(pa);
      const P = (x, y, z) => { const y1 = y * ct - z * st, z1 = y * st + z * ct, X = x * cp - y1 * sp, Y = x * sp + y1 * cp, s = 3.4 / (3.4 - z1); return [cx + X * S * s, cy + Y * S * s, z1]; };
      const D = (r, a, z) => P(r * Math.cos(a), r * Math.sin(a), z || 0);
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'lighter';
      for (const n of NEB) { const q = D(n.r * radK, n.th + rotA), rr = n.s * S * (1 + .1 * Math.sin(t * .0006 + n.r * 9)); blob(q, rr, 'rgba(' + n.c + ',' + (mini ? .1 : .13) + ')', 'rgba(' + n.c + ',0)'); }
      // dark dust lanes trailing each arm
      ctx.globalCompositeOperation = 'source-over';
      for (const d of DUST) { const q = D(d.r * radK, d.th + rotA + cur.wind * d.r * 1.6), rr = d.s * S; blob(q, rr, 'rgba(6,3,16,.55)', 'rgba(6,3,16,0)'); }
      ctx.globalCompositeOperation = 'lighter';
      const tn = cur.ta > .01;
      for (const p of ST) {
        let r = p.r * radK, a = p.th + rotA + cur.wind * p.r * 1.6 + .04 * Math.sin(rotA * .6 + p.r * 7), bb = p.b * (1 + flare);
        if (p.tw) bb *= .75 + .25 * Math.sin(t * .004 * p.sp + p.ph);
        if (mode === 'listen') { bb *= 1 + 1.6 * amp * Math.pow(Math.max(0, Math.sin(p.r * 22 - t * .012)), 4); r *= 1 + .03 * amp * Math.sin(p.r * 22 - t * .012); }
        else if (mode === 'speak') { r *= 1 + .03 * amp; bb *= 1 + amp * .9 * Math.max(0, 1 - p.r * 2.5); if (p.arm) bb *= 1 + .9 * amp * Math.pow(Math.max(0, Math.sin(p.r * 9 - t * .007)), 3); }
        else if (mode === 'music') { const wv = Math.max(0, Math.sin(p.r * 13 - t * .009)); bb *= 1 + 1.3 * amp * wv; r *= 1 + .025 * amp * wv; }
        else if (mode === 'embed') { p.life -= dt * .00035 * p.sp; if (p.life < 0) p.life += 1; const L = p.life; r *= .08 + .92 * L; a += (1 - L) * 2.4; bb *= .6 + .7 * (1 - L); }
        else if (mode === 'alert') bb *= .85 + .15 * Math.sin(t * .03 + p.th * 5);
        const q = D(r, a, p.z), d = (q[2] + 1) / 2, al = Math.min(1, bb * (.55 + .6 * d));
        const c = tn ? [p.c[0] + (cur.tint[0] - p.c[0]) * cur.ta, p.c[1] + (cur.tint[1] - p.c[1]) * cur.ta, p.c[2] + (cur.tint[2] - p.c[2]) * cur.ta] : p.c;
        const col = 'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',';
        const z = p.sz * sc * (.8 + .5 * d);
        ctx.fillStyle = col + al.toFixed(3) + ')'; ctx.fillRect(q[0] - z / 2, q[1] - z / 2, z, z);
        if (p.hot && !mini) { ctx.fillStyle = col + (.35 * al).toFixed(3) + ')'; ctx.fillRect(q[0] - z * 2.2, q[1] - .35 * sc, z * 4.4, .7 * sc); ctx.fillRect(q[0] - .35 * sc, q[1] - z * 2.2, .7 * sc, z * 4.4); }
      }
      // bright star-forming knots along the arms
      for (const k of KNOT) { const q = D(k.r * radK, k.th + rotA + cur.wind * k.r * 1.6), f = .7 + .3 * Math.sin(t * .002 + k.ph); blob(q, 7 * sc * f, 'rgba(' + k.c + ',' + (.55 * f).toFixed(2) + ')', 'rgba(' + k.c + ',0)'); ctx.fillStyle = 'rgba(255,250,255,' + (.8 * f).toFixed(2) + ')'; ctx.fillRect(q[0] - sc, q[1] - sc, 2 * sc, 2 * sc); }
      // listening: rings of light roll outward; alert: rose rings pull inward
      if (mode === 'listen' || mode === 'alert') {
        for (let i = 0; i < 3; i++) {
          let f = ((t * .00045) + i / 3) % 1; if (mode === 'alert') f = 1 - f;
          const R = .12 + f * 1.0, alpha = (mode === 'alert' ? .5 : .35) * Math.sin(f * Math.PI), col = mode === 'alert' ? '255,120,150' : '215,195,255';
          let pv = null; for (let j = 0; j <= 72; j++) { const q = D(R, j / 72 * 6.283); if (pv) line(pv, q, 'rgba(' + col + ',' + alpha.toFixed(3) + ')', 1.2); pv = q; }
        }
      }
      // polar jets while thinking
      if (cur.jet > .02) {
        for (const sgn of [1, -1]) { const a0 = P(0, 0, .04 * sgn), a1 = P(0, 0, (mini ? .7 : .95) * sgn), g = ctx.createLinearGradient(a0[0], a0[1], a1[0], a1[1]); g.addColorStop(0, 'rgba(235,215,255,' + (.6 * cur.jet).toFixed(3) + ')'); g.addColorStop(1, 'rgba(190,160,255,0)'); ctx.strokeStyle = g; ctx.lineWidth = 3 * sc; ctx.beginPath(); ctx.moveTo(a0[0], a0[1]); ctx.lineTo(a1[0], a1[1]); ctx.stroke(); }
        for (const j of JET) { j.u += dt * .0005 * j.sp * (1 + cur.spin * .4); if (j.u > 1) j.u -= 1; const sp2 = .015 + j.u * .07, q = P(j.jx * sp2, j.jy * sp2, j.sgn * (.05 + j.u * (mini ? .65 : .9))), al = (1 - j.u) * cur.jet; ctx.fillStyle = 'rgba(240,225,255,' + al.toFixed(3) + ')'; const z = (1.6 - j.u) * sc; ctx.fillRect(q[0] - z / 2, q[1] - z / 2, z, z); }
      }
      // aura rings in the disc plane
      const ring = (R, n, w, alpha, dash) => { let pv = null; for (let i = 0; i <= n; i++) { const q = D(R, i / n * 6.283); if (pv && !(dash && i % 2)) line(pv, q, 'rgba(200,175,255,' + (alpha * (.35 + .65 * (q[2] + 1) / 2)).toFixed(3) + ')', w); pv = q; } };
      if (!o.icon) ring(1.1, 140, 1, .35);
      if (!mini && !o.icon) ring(1.0, 120, 1, .28, true);
      if (!o.icon) { let pv = null; const sw = t * .0009 * (1 + cur.spin); for (let i = 0; i <= 18; i++) { const q = D(1.1, sw + i * .035); if (pv) line(pv, q, 'rgba(250,240,255,' + (i / 18).toFixed(2) + ')', 2.4); pv = q; } }
      if (cur.comet > .02) {
        [[.62, 1.6], [.85, 1.2], [1.02, .9]].forEach((oc, i) => {
          if (mini && i === 2) return;
          const ph = t * .001 * oc[1] * (1 + cur.spin * .5) + i * 2.1, pts = [];
          for (let j = 0; j <= 18; j++) { const an = ph - j * .035; pts.push(P(Math.cos(an) * oc[0], Math.sin(an) * oc[0], .08 * Math.sin(an * 2 + i))); }
          for (let j = 1; j < pts.length; j++) line(pts[j - 1], pts[j], 'rgba(' + (i === 1 ? '255,210,150' : '230,215,255') + ',' + ((1 - j / 18) * .9 * cur.comet).toFixed(3) + ')', 2.2 * (1 - j / 22));
          blob(pts[0], 9 * sc, 'rgba(255,250,255,' + cur.comet.toFixed(2) + ')', 'rgba(200,170,255,0)');
        });
      }
      // the core: bulge, diffraction spikes, and a hot accretion ring
      const c0 = P(0, 0, 0), r0 = S * .16 * (1 + .15 * amp + (sk < 500 ? .3 * (1 - sk / 500) : 0)), gl = cur.glow * (1 + flare * .6) * (mode === 'alert' ? (.8 + .2 * Math.sin(t * .03)) : 1);
      blob(c0, r0 * 3.2, 'rgba(255,225,245,' + (.5 * gl).toFixed(3) + ')', 'rgba(160,120,255,0)');
      blob(c0, r0, 'rgba(255,253,255,' + Math.min(1, gl).toFixed(3) + ')', 'rgba(244,140,214,0)');
      for (let i = 0; i < 2; i++) { const R = .055 + i * .03; let pv = null; for (let j = 0; j <= 64; j++) { const an = j / 64 * 6.283, q = D(R, an), dop = .5 + .5 * Math.cos(an - rotA * 6 - 1); if (pv) line(pv, q, 'rgba(255,' + (215 + 30 * dop | 0) + ',245,' + (cur.acc * (.25 + .6 * dop) * (i ? .55 : 1)).toFixed(3) + ')', (i ? 1 : 1.8)); pv = q; } }
      for (const an of [pa, pa + Math.PI / 2]) {
        const L = r0 * (o.icon ? 2.2 : 3.4 + amp * 1.2), dx = Math.cos(an) * L, dy = Math.sin(an) * L, g = ctx.createLinearGradient(c0[0] - dx, c0[1] - dy, c0[0] + dx, c0[1] + dy);
        g.addColorStop(0, 'rgba(255,240,255,0)'); g.addColorStop(.5, 'rgba(255,245,255,' + (.75 * gl).toFixed(3) + ')'); g.addColorStop(1, 'rgba(255,240,255,0)');
        ctx.strokeStyle = g; ctx.lineWidth = 1.4 * sc; ctx.beginPath(); ctx.moveTo(c0[0] - dx, c0[1] - dy); ctx.lineTo(c0[0] + dx, c0[1] + dy); ctx.stroke();
      }
      if (sk < 900) { const f = sk / 900; let pv = null; for (let i = 0; i <= 64; i++) { const q = D(.15 + 1.0 * f, i / 64 * 6.283); if (pv) line(pv, q, 'rgba(225,205,255,' + (.7 * (1 - f)).toFixed(3) + ')', 2.2 - 1.6 * f); pv = q; } }
      ctx.globalCompositeOperation = 'source-over';
      self.rafs[key] = requestAnimationFrame(frame);
    };
    this.rafs = this.rafs || {}; this.rafs[key] = requestAnimationFrame(frame);
  }
